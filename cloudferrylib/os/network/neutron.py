# Copyright (c) 2014 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the License);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and#
# limitations under the License.


import ipaddr

from neutronclient.common.exceptions import IpAddressGenerationFailureClient
from neutronclient.v2_0 import client as neutron_client

from cloudferrylib.base import network
from cloudferrylib.utils import utils as utl


LOG = utl.get_log(__name__)
DEFAULT_SECGR = 'default'
SERVICES_TENANT_NAME = 'services'


class NeutronNetwork(network.Network):

    """
    The main class for working with OpenStack Neutron client
    """

    def __init__(self, config, cloud):
        super(NeutronNetwork, self).__init__(config)
        self.cloud = cloud
        self.identity_client = cloud.resources['identity']
        self.neutron_client = self.proxy(self.get_client(), config)

    def get_client(self):
        return neutron_client.Client(
            username=self.config.cloud.user,
            password=self.config.cloud.password,
            tenant_name=self.config.cloud.tenant,
            auth_url=self.config.cloud.auth_url)

    def read_info(self, **kwargs):

        """Get info about neutron resources:
        :rtype: Dictionary with all necessary neutron info
        """

        tenant_id = ''
        if not self.config.migrate.all_networks:
            tenant_name = self.config.cloud.tenant
            tenant_id = self.identity_client.get_tenant_id_by_name(tenant_name)

        info = {'networks': self.get_networks(tenant_id),
                'subnets': self.get_subnets(tenant_id),
                'routers': self.get_routers(tenant_id),
                'floating_ips': self.get_floatingips(tenant_id),
                'security_groups': self.get_sec_gr_and_rules(tenant_id),
                'meta': {}}
        if self.config.migrate.keep_lbaas:
            info['lbaas'] = dict()
            info['lb_pools'] = self.get_lb_pools()
            info['lb_monitors'] = self.get_lb_monitors()
            info['lb_members'] = self.get_lb_members()
            info['lb_vips'] = self.get_lb_vips()
        return info

    def deploy(self, info):
        deploy_info = info
        self.upload_networks(deploy_info['networks'])
        self.upload_subnets(deploy_info['networks'],
                            deploy_info['subnets'])
        self.upload_routers(deploy_info['networks'],
                            deploy_info['subnets'],
                            deploy_info['routers'])
        if self.config.migrate.keep_floatingip:
            self.upload_floatingips(deploy_info['networks'],
                                    deploy_info['floating_ips'])
        self.upload_neutron_security_groups(deploy_info['security_groups'])
        self.upload_sec_group_rules(deploy_info['security_groups'])
        if self.config.migrate.keep_lbaas:
            self.upload_lb_pools(deploy_info['lb_pools'],
                                 deploy_info['subnets'])
            self.upload_lb_monitors(deploy_info['lb_monitors'])
            self.associate_lb_monitors(deploy_info['lb_pools'],
                                    deploy_info['lb_monitors'])
            self.upload_lb_members(deploy_info['lb_members'],
                                   deploy_info['lb_pools'])
            self.upload_lb_vips(deploy_info['lb_vips'],
                                deploy_info['lb_pools'],
                                deploy_info['subnets'])

    def get_func_mac_address(self, instance):
        return self.get_mac_by_ip

    def get_mac_by_ip(self, ip_address):
        for port in self.get_list_ports():
            for fixed_ip_info in port['fixed_ips']:
                if fixed_ip_info['ip_address'] == ip_address:
                    return port["mac_address"]

    def get_list_ports(self, **kwargs):
        return self.neutron_client.list_ports(**kwargs)['ports']

    def create_port(self, net_id, mac, ip, tenant_id, keep_ip, sg_ids=None):
        param_create_port = {'network_id': net_id,
                             'mac_address': mac,
                             'tenant_id': tenant_id}
        if sg_ids:
            param_create_port['security_groups'] = sg_ids
        if keep_ip:
            param_create_port['fixed_ips'] = [{"ip_address": ip}]
        return self.neutron_client.create_port({
            'port': param_create_port})['port']

    def delete_port(self, port_id):
        return self.neutron_client.delete_port(port_id)

    def get_security_groups_list(self, **kwargs):
        return self.neutron_client.\
            list_security_groups(**kwargs)['security_groups']

    def get_network(self, network_info, tenant_id, keep_ip=False):
        if keep_ip:
            instance_addr = ipaddr.IPAddress(network_info['ip'])
            for snet in self.neutron_client.list_subnets()['subnets']:
                if snet['tenant_id'] == tenant_id:
                    if ipaddr.IPNetwork(snet['cidr']).Contains(instance_addr):
                        return self.neutron_client.\
                            list_networks(id=snet['network_id'])['networks'][0]
        if 'id' in network_info:
            return self.neutron_client.\
                list_networks(id=network_info['id'])['networks'][0]
        if 'name' in network_info:
            return self.neutron_client.\
                list_networks(name=network_info['name'])['networks'][0]
        else:
            raise Exception("Can't find suitable network")

    def check_existing_port(self, network_id, mac):
        for port in self.get_list_ports(fields=['network_id',
                                                'mac_address', 'id']):
            if (port['network_id'] == network_id) \
                    and (port['mac_address'] == mac):
                return port['id']
        return None

    @staticmethod
    def convert(neutron_object, cloud, obj_name):
        """Convert OpenStack Neutron network object to CloudFerry object.

        :param neutron_object: Direct OS NeutronNetwork object to convert,
        :cloud:                Cloud object,
        :obj_name:             Name of NeutronNetwork object to convert.
                               List of possible values:
                               'network', 'subnet', 'router', 'floating_ip',
                               'security_group', 'rule'.
        """

        obj_map = {
            'network': NeutronNetwork.convert_networks,
            'subnet': NeutronNetwork.convert_subnets,
            'router': NeutronNetwork.convert_routers,
            'floating_ip': NeutronNetwork.convert_floatingips,
            'security_group': NeutronNetwork.convert_security_groups,
            'rule': NeutronNetwork.convert_rules,
            'lb_pool': NeutronNetwork.convert_lb_pools,
            'lb_member': NeutronNetwork.convert_lb_members,
            'lb_monitor': NeutronNetwork.convert_lb_monitors,
            'lb_vip': NeutronNetwork.convert_lb_vips
        }

        return obj_map[obj_name](neutron_object, cloud)

    @staticmethod
    def convert_networks(net, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]
        get_tenant_name = identity_res.get_tenants_func()

        subnet_names = []
        for subnet in net['subnets']:
            name = net_res.neutron_client.show_subnet(subnet)['subnet']['name']
            subnet_names.append(name)

        result = {
            'name': net['name'],
            'id': net['id'],
            'admin_state_up': net['admin_state_up'],
            'shared': net['shared'],
            'tenant_id': net['tenant_id'],
            'tenant_name': get_tenant_name(net['tenant_id']),
            'subnet_names': subnet_names,
            'router:external': net['router:external'],
            'provider:physical_network': net['provider:physical_network'],
            'provider:network_type': net['provider:network_type'],
            'provider:segmentation_id': net['provider:segmentation_id'],
            'meta': {},
        }

        res_hash = net_res.get_resource_hash(result,
                                             'name',
                                             'shared',
                                             'tenant_name',
                                             'router:external')
        result['res_hash'] = res_hash
        return result

    @staticmethod
    def convert_subnets(snet, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        network_res = cloud.resources[utl.NETWORK_RESOURCE]
        get_tenant_name = identity_res.get_tenants_func()

        net = network_res.neutron_client.show_network(snet['network_id'])

        result = {
            'name': snet['name'],
            'id': snet['id'],
            'enable_dhcp': snet['enable_dhcp'],
            'allocation_pools': snet['allocation_pools'],
            'gateway_ip': snet['gateway_ip'],
            'ip_version': snet['ip_version'],
            'cidr': snet['cidr'],
            'network_name': net['network']['name'],
            'external': net['network']['router:external'],
            'network_id': snet['network_id'],
            'tenant_name': get_tenant_name(snet['tenant_id']),
            'meta': {},
        }

        res_hash = network_res.get_resource_hash(result,
                                                 'name',
                                                 'enable_dhcp',
                                                 'allocation_pools',
                                                 'gateway_ip',
                                                 'cidr',
                                                 'tenant_name')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_routers(router, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        ips = []
        subnet_ids = []

        for port in net_res.neutron_client.list_ports()['ports']:
            if port['device_id'] == router['id']:
                for ip_info in port['fixed_ips']:
                    ips.append(ip_info['ip_address'])
                    if ip_info['subnet_id'] not in subnet_ids:
                        subnet_ids.append(ip_info['subnet_id'])

        result = {
            'name': router['name'],
            'id': router['id'],
            'admin_state_up': router['admin_state_up'],
            'routes': router['routes'],
            'external_gateway_info': router['external_gateway_info'],
            'tenant_name': get_tenant_name(router['tenant_id']),
            'ips': ips,
            'subnet_ids': subnet_ids,
            'meta': {},
        }

        if router['external_gateway_info']:
            ext_id = router['external_gateway_info']['network_id']
            ext_net = net_res.neutron_client.show_network(ext_id)['network']

            result['ext_net_name'] = ext_net['name']
            result['ext_net_tenant_name'] = get_tenant_name(
                ext_net['tenant_id'])
            result['ext_net_id'] = router['external_gateway_info'][
                'network_id']

        res_hash = net_res.get_resource_hash(result,
                                             'name',
                                             'routes',
                                             'tenant_name')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_floatingips(floating, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        ext_id = floating['floating_network_id']
        extnet = net_res.neutron_client.show_network(ext_id)['network']

        result = {
            'id': floating['id'],
            'tenant_id': floating['tenant_id'],
            'floating_network_id': ext_id,
            'network_name': extnet['name'],
            'ext_net_tenant_name': get_tenant_name(extnet['tenant_id']),
            'tenant_name': get_tenant_name(floating['tenant_id']),
            'fixed_ip_address': floating['fixed_ip_address'],
            'floating_ip_address': floating['floating_ip_address'],
            'meta': {},
        }

        return result

    @staticmethod
    def convert_rules(rule, cloud):
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        rule_hash = net_res.get_resource_hash(rule,
                                              'direction',
                                              'remote_ip_prefix',
                                              'protocol',
                                              'port_range_min',
                                              'port_range_max',
                                              'ethertype')

        result = {
            'remote_group_id': rule['remote_group_id'],
            'direction': rule['direction'],
            'remote_ip_prefix': rule['remote_ip_prefix'],
            'protocol': rule['protocol'],
            'port_range_min': rule['port_range_min'],
            'port_range_max': rule['port_range_max'],
            'ethertype': rule['ethertype'],
            'security_group_id': rule['security_group_id'],
            'rule_hash': rule_hash,
            'meta': dict()
        }

        return result

    @staticmethod
    def convert_security_groups(sec_gr, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        security_group_rules = []
        for rule in sec_gr['security_group_rules']:
            rule_info = NeutronNetwork.convert(rule, cloud, 'rule')
            security_group_rules.append(rule_info)

        result = {
            'name': sec_gr['name'],
            'id': sec_gr['id'],
            'tenant_id': sec_gr['tenant_id'],
            'tenant_name': get_tenant_name(sec_gr['tenant_id']),
            'description': sec_gr['description'],
            'security_group_rules': security_group_rules,
            'meta': {},
        }

        res_hash = net_res.get_resource_hash(result,
                                             'name',
                                             'tenant_name',
                                             'description')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_lb_pools(pool, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        result = {
            'name': pool['name'],
            'id': pool['id'],
            'description': pool['description'],
            'lb_method': pool['lb_method'],
            'protocol': pool['protocol'],
            'provider': pool['provider'],
            'subnet_id': pool['subnet_id'],
            'tenant_id': pool['tenant_id'],
            'tenant_name': get_tenant_name(pool['tenant_id']),
            'health_monitors': pool['health_monitors'],
            'members': pool['members'],
            'meta': {}
        }

        res_hash = net_res.get_resource_hash(result,
                                             'name',
                                             'tenant_name',
                                             'lb_method',
                                             'protocol',
                                             'provider')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_lb_monitors(monitor, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        result = {
            'id': monitor['id'],
            'tenant_id': monitor['tenant_id'],
            'tenant_name': get_tenant_name(monitor['tenant_id']),
            'type': monitor['type'],
            'delay': monitor['delay'],
            'timeout': monitor['timeout'],
            'max_retries': monitor['max_retries'],
            'url_path': monitor.get('url_path', None),
            'expected_codes': monitor.get('expected_codes', None),
            'pools': monitor['pools'],
            'meta': {}
        }

        res_hash = net_res.get_resource_hash(result,
                                             'tenant_name',
                                             'type',
                                             'delay',
                                             'timeout',
                                             'max_retries')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_lb_members(member, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        result = {
            'id': member['id'],
            'pool_id': member['pool_id'],
            'address': member['address'],
            'protocol_port': member['protocol_port'],
            'weight': member['weight'],
            'tenant_id': member['tenant_id'],
            'tenant_name': get_tenant_name(member['tenant_id']),
            'meta': {}
        }

        res_hash = net_res.get_resource_hash(result,
                                             'address',
                                             'protocol_port',
                                             'weight',
                                             'tenant_name')

        result['res_hash'] = res_hash

        return result

    @staticmethod
    def convert_lb_vips(vip, cloud):
        identity_res = cloud.resources[utl.IDENTITY_RESOURCE]
        net_res = cloud.resources[utl.NETWORK_RESOURCE]

        get_tenant_name = identity_res.get_tenants_func()

        result = {
            'name': vip['name'],
            'id': vip['id'],
            'description': vip['description'],
            'address': vip['address'],
            'protocol': vip['protocol'],
            'protocol_port': vip['protocol_port'],
            'pool_id': vip['pool_id'],
            'connection_limit': vip['connection_limit'],
            'session_persistence': vip.get('session_persistence', None),
            'tenant_id': vip['tenant_id'],
            'subnet_id': vip['subnet_id'],
            'tenant_name': get_tenant_name(vip['tenant_id']),
            'meta': {}
        }

        res_hash = net_res.get_resource_hash(result,
                                             'name',
                                             'address',
                                             'protocol',
                                             'protocol_port',
                                             'tenant_name')

        result['res_hash'] = res_hash

        return result

    def get_networks(self, tenant_id=''):
        networks = self.get_networks_list(tenant_id)
        networks_info = []

        for net in networks:
            cf_net = self.convert(net, self.cloud, 'network')
            networks_info.append(cf_net)

        return networks_info

    def get_networks_list(self, tenant_id=''):
        return self.neutron_client.list_networks(
            tenant_id=tenant_id)['networks']

    def get_subnets(self, tenant_id=''):
        subnets = self.neutron_client.list_subnets(
            tenant_id=tenant_id)['subnets']
        subnets_info = []

        for snet in subnets:
            subnet = self.convert(snet, self.cloud, 'subnet')
            subnets_info.append(subnet)

        return subnets_info

    def reset_subnet_dhcp(self, subnet_id, dhcp_flag):
        subnet_info = {
            'subnet':
            {
                'enable_dhcp': dhcp_flag
            }
        }
        return self.neutron_client.update_subnet(subnet_id, subnet_info)

    def get_routers(self, tenant_id=''):
        routers = self.neutron_client.list_routers(
            tenant_id=tenant_id)['routers']
        routers_info = []

        for router in routers:
            rinfo = self.convert(router, self.cloud, 'router')
            routers_info.append(rinfo)

        return routers_info

    def get_floatingips(self, tenant_id=''):
        floatings = self.neutron_client.list_floatingips(
            tenant_id=tenant_id)['floatingips']
        floatingips_info = []

        for floating in floatings:
            floatingip_info = self.convert(floating, self.cloud, 'floating_ip')
            floatingips_info.append(floatingip_info)

        return floatingips_info

    def get_security_groups(self, tenant_id=''):
        sec_grs = self.neutron_client.list_security_groups(
            tenant_id=tenant_id)['security_groups']
        return sec_grs

    def get_sec_gr_and_rules(self, tenant_id=''):
        sec_grs = self.get_security_groups(tenant_id)
        sec_groups_info = []

        for sec_gr in sec_grs:
            sec_gr_info = self.convert(sec_gr, self.cloud, 'security_group')
            sec_groups_info.append(sec_gr_info)

        return sec_groups_info

    def get_lb_pools(self):
        pools = self.neutron_client.list_pools()['pools']
        pools_info = []

        for pool in pools:
            pool_info = self.convert(pool, self.cloud, 'lb_pool')
            pools_info.append(pool_info)

        return pools_info

    def get_lb_monitors(self):
        monitors = \
            self.neutron_client.list_health_monitors()['health_monitors']
        monitors_info = []

        for mon in monitors:
            mon_info = self.convert(mon, self.cloud, 'lb_monitor')
            monitors_info.append(mon_info)

        return monitors_info

    def get_lb_members(self):
        members = self.neutron_client.list_members()['members']
        members_info = []

        for member in members:
            member_info = self.convert(member, self.cloud, 'lb_member')
            members_info.append(member_info)

        return members_info

    def get_lb_vips(self):
        vips = self.neutron_client.list_vips()['vips']
        vips_info = []

        for vip in vips:
            vip_info = self.convert(vip, self.cloud, 'lb_vip')
            vips_info.append(vip_info)

        return vips_info

    def upload_lb_vips(self, vips, pools, subnets):
        existing_vips = self.get_lb_vips()
        existing_vips_hashlist = [ex_vip['res_hash'] for ex_vip in existing_vips]
        existing_pools = self.get_lb_pools()
        existing_snets = self.get_subnets()
        for vip in vips:
            if vip['res_hash'] not in existing_vips_hashlist:
                tenant_id = \
                    self.identity_client.get_tenant_id_by_name(vip['tenant_name'])
                pool_hash = self.get_res_hash_by_id(pools, vip['pool_id'])
                dst_pool = self.get_res_by_hash(existing_pools, pool_hash)
                snet_hash = self.get_res_hash_by_id(subnets, vip['subnet_id'])
                dst_subnet = self.get_res_by_hash(existing_snets, snet_hash)
                vip_info = {
                    'vip': {
                        'name': vip['name'],
                        'description': vip['description'],
                        'address': vip['address'],
                        'protocol': vip['protocol'],
                        'protocol_port': vip['protocol_port'],
                        'connection_limit': vip['connection_limit'],
                        'pool_id': dst_pool['id'],
                        'tenant_id': tenant_id,
                        'subnet_id': dst_subnet['id']
                    }
                }
                if vip['session_persistence']:
                    vip_info['vip']['session_persistence'] = vip['session_persistence']
                vip['meta']['id'] = \
                    self.neutron_client.create_vip(vip_info)['vip']['id']
            else:
                LOG.info("| Dst cloud already has the same VIP "
                         "with address %s in tenant %s" %
                         (vip['address'], vip['tenant_name']))


    def upload_lb_members(self, members, pools):
        existing_members = self.get_lb_members()
        existing_members_hashlist = \
            [ex_member['res_hash'] for ex_member in existing_members]
        existing_pools = self.get_lb_pools()
        for member in members:
            if member['res_hash'] not in existing_members_hashlist:
                tenant_id = \
                    self.identity_client.get_tenant_id_by_name(member['tenant_name'])
                pool_hash = self.get_res_hash_by_id(pools, member['pool_id'])
                dst_pool = self.get_res_by_hash(existing_pools, pool_hash)
                member_info = {
                    'member': {
                        'protocol_port': member["protocol_port"],
                        'address': member['address'],
                        'pool_id': dst_pool['id']
                    }
                }
                member['meta']['id'] = \
                    self.neutron_client.create_member(member_info)['member']['id']
            else:
                LOG.info("| Dst cloud already has the same member "
                         "with address %s in tenant %s" %
                         (member['address'], member['tenant_name']))


    def upload_lb_monitors(self, monitors):
        existing_mons = self.get_lb_monitors()
        existing_mons_hashlist = \
            [ex_mon['res_hash'] for ex_mon in existing_mons]
        for mon in monitors:
            if mon['res_hash'] not in existing_mons_hashlist:
                tenant_id = \
                    self.identity_client.get_tenant_id_by_name(mon['tenant_name'])
                mon_info = {
                    'health_monitor':
                        {
                            'tenant_id': tenant_id,
                            'type': mon['type'],
                            'delay': mon['delay'],
                            'timeout': mon['timeout'],
                            'max_retries': mon['max_retries']
                        }
                }
                if mon['url_path']:
                    mon_info['health_monitor']['url_path'] = mon['url_path']
                    mon_info['health_monitor']['expected_codes'] = mon['expected_codes']
                mon['meta']['id'] = \
                    self.neutron_client.create_health_monitor(mon_info)['health_monitor']['id']
            else:
                LOG.info("| Dst cloud already has the same healthmonitor "
                         "with type %s in tenant %s" %
                         (mon['type'], mon['tenant_name']))

    def associate_lb_monitors(self, pools, monitors):
        existing_pools = self.get_lb_pools()
        existing_monitors = self.get_lb_monitors()
        for pool in pools:
            pool_hash = self.get_res_hash_by_id(pools, pool['id'])
            dst_pool = self.get_res_by_hash(existing_pools, pool_hash)
            for monitor_id in pool['health_monitors']:
                monitor_hash = self.get_res_hash_by_id(monitors, monitor_id)
                dst_monitor = self.get_res_by_hash(existing_monitors, monitor_hash)
                if dst_monitor['id'] not in dst_pool['health_monitors']:
                    dst_monitor_info = {
                        'health_monitor':{
                            'id': dst_monitor['id']
                        }
                    }
                    self.neutron_client.associate_health_monitor(dst_pool['id'],
                                                                 dst_monitor_info)
                else:
                    LOG.info("| Dst pool with name %s already has associated the healthmonitor "
                         "with id %s in tenant %s" %
                         (dst_pool['name'], dst_monitor['id'], dst_monitor['tenant_name']))

    def upload_lb_pools(self, pools, subnets):
        existing_pools = self.get_lb_pools()
        existing_pools_hashlist = \
            [ex_pool['res_hash'] for ex_pool in existing_pools]
        existing_subnets = self.get_subnets()
        for pool in pools:
            if pool['res_hash'] not in existing_pools_hashlist:
                tenant_id = \
                    self.identity_client.get_tenant_id_by_name(pool['tenant_name'])
                snet_hash = self.get_res_hash_by_id(subnets, pool['subnet_id'])
                snet_id = self.get_res_by_hash(existing_subnets,
                                               snet_hash)['id']
                pool_info = {
                    'pool':
                        {
                            'name': pool['name'],
                            'description': pool['description'],
                            'tenant_id': tenant_id,
                            'provider': pool['provider'],
                            'subnet_id': snet_id,
                            'protocol': pool['protocol'],
                            'lb_method': pool['lb_method']
                            }
                }
                pool['meta']['id'] = \
                    self.neutron_client.create_pool(pool_info)['pool']['id']
            else:
                LOG.info("| Dst cloud already has the same pool "
                         "with name %s in tenant %s" %
                         (pool['name'], pool['tenant_name']))

    def upload_neutron_security_groups(self, sec_groups):
        exist_secgrs = self.get_sec_gr_and_rules()
        exis_secgrs_hashlist = [ex_sg['res_hash'] for ex_sg in exist_secgrs]
        for sec_group in sec_groups:
            if sec_group['name'] != DEFAULT_SECGR:
                if sec_group['res_hash'] not in exis_secgrs_hashlist:
                    tenant_id = \
                        self.identity_client.get_tenant_id_by_name(
                            sec_group['tenant_name']
                        )
                    sg_info = \
                        {
                            'security_group':
                            {
                                'name': sec_group['name'],
                                'tenant_id': tenant_id,
                                'description': sec_group['description']
                            }
                        }
                    sec_group['meta']['id'] = self.neutron_client.\
                        create_security_group(sg_info)['security_group']['id']

    def upload_sec_group_rules(self, sec_groups):
        ex_secgrs = self.get_sec_gr_and_rules()
        for sec_gr in sec_groups:
            if sec_gr['tenant_name'] != SERVICES_TENANT_NAME:
                ex_secgr = \
                    self.get_res_by_hash(ex_secgrs, sec_gr['res_hash'])
                exrules_hlist = \
                    [r['rule_hash'] for r in ex_secgr['security_group_rules']]
                for rule in sec_gr['security_group_rules']:
                    if rule['protocol'] \
                            and (rule['rule_hash'] not in exrules_hlist):
                        rinfo = \
                            {'security_group_rule': {
                                'direction': rule['direction'],
                                'protocol': rule['protocol'],
                                'port_range_min': rule['port_range_min'],
                                'port_range_max': rule['port_range_min'],
                                'ethertype': rule['ethertype'],
                                'remote_ip_prefix': rule['remote_ip_prefix'],
                                'security_group_id': ex_secgr['id'],
                                'tenant_id': ex_secgr['tenant_id']}}
                        if rule['remote_group_id']:
                            remote_sghash = \
                                self.get_res_hash_by_id(sec_groups,
                                                        rule['remote_group_id'])
                            rem_ex_sec_gr = \
                                self.get_res_by_hash(ex_secgrs,
                                                     remote_sghash)
                            rinfo['security_group_rule']['remote_group_id'] = \
                                rem_ex_sec_gr['id']
                        new_rule = \
                            self.neutron_client.create_security_group_rule(rinfo)
                        rule['meta']['id'] = new_rule['security_group_rule']['id']

    def upload_networks(self, networks):
        existing_nets_hashlist = (
            [ex_net['res_hash'] for ex_net in self.get_networks()])
        for net in networks:
            tenant_id = \
                self.identity_client.get_tenant_id_by_name(net['tenant_name'])
            network_info = {
                'network':
                {
                    'name': net['name'],
                    'admin_state_up': net['admin_state_up'],
                    'tenant_id': tenant_id,
                    'shared': net['shared'],
                    'router:external': net['router:external'],
                    'provider:physical_network': \
                       net['provider:physical_network'],
                    'provider:network_type':  net['provider:network_type']
                }
            }
            if net['router:external']:
                if not self.config.migrate.migrate_extnets:
                    continue
            if net['provider:network_type'] == 'vlan':
                network_info['network']['provider:segmentation_id'] = (
                    net['provider:segmentation_id'])
            if net['res_hash'] not in existing_nets_hashlist:
                net['meta']['id'] = (self.neutron_client.
                    create_network(network_info)['network']['id'])
            else:
                LOG.info("| Dst cloud already has the same network "
                         "with name %s in tenant %s" %
                         (net['name'], net['tenant_name']))

    def upload_subnets(self, networks, subnets):
        existing_nets = self.get_networks()
        existing_subnets_hashlist = \
            [ex_snet['res_hash'] for ex_snet in self.get_subnets()]
        for snet in subnets:
            if snet['external'] and not self.config.migrate.migrate_extnets:
                continue
            tenant_id = \
                self.identity_client.get_tenant_id_by_name(snet['tenant_name'])
            net_hash = \
                self.get_res_hash_by_id(networks, snet['network_id'])
            network = \
                self.get_res_by_hash(existing_nets, net_hash)
            subnet_info = {
                'subnet':
                {
                    'name': snet['name'],
                    'enable_dhcp': snet['enable_dhcp'],
                    'network_id': network['id'],
                    'cidr': snet['cidr'],
                    'allocation_pools': snet['allocation_pools'],
                    'gateway_ip': snet['gateway_ip'],
                    'ip_version': snet['ip_version'],
                    'tenant_id': tenant_id
                }
            }
            if snet['res_hash'] not in existing_subnets_hashlist:
                snet['meta']['id'] = self.neutron_client.\
                    create_subnet(subnet_info)['subnet']['id']
            else:
                LOG.info("| Dst cloud already has the same subnetwork "
                         "with name %s in tenant %s" %
                         (snet['name'], snet['tenant_name']))

    def upload_routers(self, networks, subnets, routers):
        existing_nets = self.get_networks()
        existing_subnets = self.get_subnets()
        existing_routers = self.get_routers()
        existing_routers_hashlist = \
            [ex_router['res_hash'] for ex_router in existing_routers]
        for router in routers:
            tname = router['tenant_name']
            tenant_id = \
                self.identity_client.get_tenant_id_by_name(tname)
            r_info = {'router': {'name': router['name'],
                                 'tenant_id': tenant_id}}
            if router['external_gateway_info']:
                ex_net_hash = \
                    self.get_res_hash_by_id(networks, router['ext_net_id'])
                ex_net_id = \
                    self.get_res_by_hash(existing_nets, ex_net_hash)['id']
                r_info['router']['external_gateway_info'] = \
                    dict(network_id=ex_net_id)
            if router['res_hash'] not in existing_routers_hashlist:
                new_router = \
                    self.neutron_client.create_router(r_info)['router']
                router['meta']['id'] = new_router['id']
                self.add_router_interfaces(router,
                                           new_router,
                                           subnets,
                                           existing_subnets)
            else:
                existing_router = self.get_res_by_hash(existing_routers,
                                                       router['res_hash'])
                if not set(router['ips']).intersection(existing_router['ips']):
                    new_router = \
                        self.neutron_client.create_router(r_info)['router']
                    router['meta']['id'] = new_router['id']
                    self.add_router_interfaces(router,
                                               new_router,
                                               subnets,
                                               existing_subnets)
                else:
                    LOG.info("| Dst cloud already has the same router "
                             "with name %s in tenant %s" %
                             (router['name'], router['tenant_name']))

    def add_router_interfaces(self, src_router, dst_router,
                              src_snets, dst_snets):
        for snet_id in src_router['subnet_ids']:
            snet_hash = self.get_res_hash_by_id(src_snets, snet_id)
            src_net = self.get_res_by_hash(src_snets, snet_hash)
            if src_net['external']:
                continue
            ex_snet = self.get_res_by_hash(dst_snets,
                                           snet_hash)
            self.neutron_client.add_interface_router(
                dst_router['id'],
                {"subnet_id": ex_snet['id']})

    def upload_floatingips(self, networks, src_floats):
        existing_nets = self.get_networks()
        ext_nets_ids = []
        # getting list of external networks with allocated floating ips
        for src_float in src_floats:
            ext_net_hash = \
                self.get_res_hash_by_id(networks,
                                        src_float['floating_network_id'])
            ext_net_id = \
                self.get_res_by_hash(existing_nets, ext_net_hash)['id']
            if ext_net_id not in ext_nets_ids:
                ext_nets_ids.append(ext_net_id)
                self.allocate_floatingips(ext_net_id)
        existing_floatingips = self.get_floatingips()
        self.recreate_floatingips(src_floats, networks,
                                  existing_nets, existing_floatingips)
        self.delete_redundant_floatingips(src_floats, existing_floatingips)

    def allocate_floatingips(self, ext_net_id):
        try:
            while True:
                self.neutron_client.create_floatingip({
                    'floatingip': {'floating_network_id': ext_net_id}})
        except IpAddressGenerationFailureClient:
            LOG.info("| Floating IPs "
                     "were allocated in network %s" % ext_net_id)

    def recreate_floatingips(self, src_floats, src_nets,
                             existing_nets,
                             existing_floatingips):

        """ We recreate floating ips with the same parameters as on src cloud,
        because we can't determine floating ip address
        during allocation process. """

        for src_float in src_floats:
            tname = src_float['tenant_name']
            tenant_id = \
                self.identity_client.get_tenant_id_by_name(tname)
            ext_net_hash = \
                self.get_res_hash_by_id(src_nets,
                                        src_float['floating_network_id'])
            ext_net = self.get_res_by_hash(existing_nets, ext_net_hash)
            for floating in existing_floatingips:
                if floating['floating_ip_address'] == \
                        src_float['floating_ip_address']:
                    if floating['floating_network_id'] == ext_net['id']:
                        if floating['tenant_id'] != tenant_id:
                            fl_id = floating['id']
                            self.neutron_client.delete_floatingip(fl_id)
                            self.neutron_client.create_floatingip({
                                'floatingip':
                                {
                                    'floating_network_id': ext_net['id'],
                                    'tenant_id': tenant_id
                                }
                            })

    def delete_redundant_floatingips(self, src_floats, existing_floatingips):
        src_floatingips = \
            [src_float['floating_ip_address'] for src_float in src_floats]
        for floatingip in existing_floatingips:
            if floatingip['floating_ip_address'] not in src_floatingips:
                self.neutron_client.delete_floatingip(floatingip['id'])

    def update_floatingip(self, floatingip_id, port_id=None):
        update_dict = {'floatingip': {'port_id': port_id}}
        return self.neutron_client.update_floatingip(floatingip_id,
                                                     update_dict)

    @staticmethod
    def get_res_by_hash(existing_resources, resource_hash):
        for resource in existing_resources:
            if resource['res_hash'] == resource_hash:
                return resource

    @staticmethod
    def get_res_hash_by_id(resources, resource_id):
        for resource in resources:
            if resource['id'] == resource_id:
                return resource['res_hash']

    @staticmethod
    def get_resource_hash(neutron_resource, *args):
        list_info = list()
        for arg in args:
            if type(neutron_resource[arg]) is not list:
                list_info.append(neutron_resource[arg])
            else:
                for argitem in arg:
                    if type(argitem) is str:
                        argitem = argitem.lower()
                    list_info.append(argitem)
        hash_list = \
            [info.lower() if type(info) is str else info for info in list_info]
        hash_list.sort()
        return hash(tuple(hash_list))
