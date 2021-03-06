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


import copy

from fabric.api import env
from fabric.api import run
from fabric.api import settings

from cloudferrylib.base.action import action
from cloudferrylib.os.actions import convert_file_to_image
from cloudferrylib.os.actions import convert_image_to_file
from cloudferrylib.os.actions import convert_volume_to_image
from cloudferrylib.os.actions import copy_g2g
from cloudferrylib.os.actions import task_transfer
from cloudferrylib.utils import utils as utl, forward_agent

from cloudferrylib.utils.drivers import ssh_ceph_to_ceph
from cloudferrylib.utils.drivers import ssh_ceph_to_file
from cloudferrylib.utils.drivers import ssh_file_to_file
from cloudferrylib.utils.drivers import ssh_file_to_ceph


CLOUD = 'cloud'
BACKEND = 'backend'
CEPH = 'ceph'
ISCSI = 'iscsi'
COMPUTE = 'compute'
INSTANCES = 'instances'
INSTANCE_BODY = 'instance'
INSTANCE = 'instance'
DIFF = 'diff'
EPHEMERAL = 'ephemeral'
DIFF_OLD = 'diff_old'
EPHEMERAL_OLD = 'ephemeral_old'

PATH_DST = 'path_dst'
HOST_DST = 'host_dst'
PATH_SRC = 'path_src'
HOST_SRC = 'host_src'

TEMP = 'temp'
FLAVORS = 'flavors'


TRANSPORTER_MAP = {CEPH: {CEPH: ssh_ceph_to_ceph.SSHCephToCeph,
                          ISCSI: ssh_ceph_to_file.SSHCephToFile},
                   ISCSI: {CEPH: ssh_file_to_ceph.SSHFileToCeph,
                           ISCSI: ssh_file_to_file.SSHFileToFile}}


class TransportInstance(action.Action):
    # TODO constants

    def run(self, info=None, **kwargs):
        info = copy.deepcopy(info)
        #Init before run
        dst_storage = self.dst_cloud.resources[utl.STORAGE_RESOURCE]
        src_compute = self.src_cloud.resources[utl.COMPUTE_RESOURCE]
        dst_compute = self.src_cloud.resources[utl.COMPUTE_RESOURCE]
        backend_ephem_drv_src = src_compute.config.compute.backend
        backend_ephem_drv_dst = dst_compute.config.compute.backend
        backend_storage_dst = dst_storage.config.storage.backend
        new_info = {
            utl.INSTANCES_TYPE: {
            }
        }

        #Get next one instance
        for instance_id, instance in info[utl.INSTANCES_TYPE].iteritems():
            instance_boot = instance[utl.INSTANCE_BODY]['boot_mode']
            is_ephemeral = instance[utl.INSTANCE_BODY]['is_ephemeral']
            one_instance = {
                utl.INSTANCES_TYPE: {
                    instance_id: instance
                }
            }

            if instance_boot == utl.BOOT_FROM_IMAGE:
                if backend_ephem_drv_src == CEPH:
                    self.transport_image(self.dst_cloud, one_instance, instance_id)
                    one_instance = self.deploy_instance(self.dst_cloud, one_instance)
                elif backend_ephem_drv_src == ISCSI:
                    if backend_ephem_drv_dst == CEPH:
                        self.transport_diff_and_merge(self.dst_cloud, one_instance, instance_id)
                        one_instance = self.deploy_instance(self.dst_cloud, one_instance)
                    elif backend_ephem_drv_dst == ISCSI:
                        one_instance = self.deploy_instance(self.dst_cloud, one_instance)
                        self.copy_diff_file(self.src_cloud, self.dst_cloud, one_instance)
            elif instance_boot == utl.BOOT_FROM_VOLUME:
                one_instance = self.deploy_instance(self.dst_cloud, one_instance)

            if is_ephemeral:
                self.copy_ephemeral(self.src_cloud, self.dst_cloud, one_instance)
            new_info[utl.INSTANCES_TYPE].update(
                one_instance[utl.INSTANCES_TYPE])

        return {
            'info': new_info
        }

    def deploy_instance(self, dst_cloud, info):
        info = copy.deepcopy(info)
        dst_compute = dst_cloud.resources[COMPUTE]

        new_ids = dst_compute.deploy(info)
        for i in new_ids.iterkeys():
            dst_compute.wait_for_status(i, 'active')
        new_info = dst_compute.read_info(search_opts={'id': new_ids.keys()})
        for i in new_ids.iterkeys():
            dst_compute.change_status('shutoff', instance_id=i)
        for new_id, old_id in new_ids.iteritems():
            new_info['instances'][new_id]['old_id'] = old_id
            new_info['instances'][new_id]['meta'] = \
                info['instances'][old_id]['meta']
        info = self.prepare_ephemeral_drv(info, new_info, new_ids)
        return info

    def prepare_ephemeral_drv(self, info, new_info, map_new_to_old_ids):
        info = copy.deepcopy(info)
        new_info = copy.deepcopy(new_info)
        for new_id, old_id in map_new_to_old_ids.iteritems():
            instance_old = info[INSTANCES][old_id]
            instance_new = new_info[INSTANCES][new_id]

            ephemeral_path_dst = instance_new[EPHEMERAL][PATH_SRC]
            instance_new[EPHEMERAL][PATH_DST] = ephemeral_path_dst            
            ephemeral_host_dst = instance_new[EPHEMERAL][HOST_SRC]
            instance_new[EPHEMERAL][HOST_DST] = ephemeral_host_dst
            
            diff_path_dst = instance_new[DIFF][PATH_SRC]
            instance_new[DIFF][PATH_DST] = diff_path_dst            
            diff_host_dst = instance_new[DIFF][HOST_SRC]
            instance_new[DIFF][HOST_DST] = diff_host_dst

            ephemeral_path_src = instance_old[EPHEMERAL][PATH_SRC]
            instance_new[EPHEMERAL][PATH_SRC] = ephemeral_path_src            
            ephemeral_host_src = instance_old[EPHEMERAL][HOST_SRC]
            instance_new[EPHEMERAL][HOST_SRC] = ephemeral_host_src
            
            diff_path_src = instance_old[DIFF][PATH_SRC]
            instance_new[DIFF][PATH_SRC] = diff_path_src            
            diff_host_src = instance_old[DIFF][HOST_SRC]
            instance_new[DIFF][HOST_SRC] = diff_host_src

        return new_info

    def delete_remote_file_on_compute(self, path_file, host_cloud, host_instance):
        with settings(host_string=host_cloud):
            with forward_agent(env.key_filename):
                run("ssh -oStrictHostKeyChecking=no %s  'rm -rf %s'" % (host_instance, path_file))

    def find_id_by_old_id(self, info, old_id):
        for key, value in info.iteritems():
            if value['old_id'] == old_id:
                return key
        return None

    def transport_boot_volume_src_to_dst(self, src_cloud, dst_cloud, info, instance_id):
        info = copy.deepcopy(info)
        instance = info[utl.INSTANCES_TYPE][instance_id]

        src_storage = src_cloud.resources[utl.STORAGE_RESOURCE]
        volume = src_storage.read_info(id=instance[INSTANCE_BODY]['volumes'][0]['id'])

        act_v_to_i = convert_volume_to_image.ConvertVolumeToImage(self.init, cloud='src_cloud')
        image = act_v_to_i.run(volume)['image_data']

        act_g_to_g = copy_g2g.CopyFromGlanceToGlance(self.init)
        image_dst = act_g_to_g.run(image)['images_info']
        instance[utl.META_INFO][utl.IMAGE_BODY] = image_dst['image']['images'].values()[0]

        return info

    def copy_data_via_ssh(self, src_cloud, dst_cloud, info, body, resources, types):
        dst_storage = dst_cloud.resources[resources]
        src_compute = src_cloud.resources[resources]
        src_backend = src_compute.config.compute.backend
        dst_backend = dst_storage.config.compute.backend
        transporter = task_transfer.TaskTransfer(
            self.init,
            TRANSPORTER_MAP[src_backend][dst_backend],
            resource_name=types,
            resource_root_name=body)
        transporter.run(info=info)

    def copy_diff_file(self, src_cloud, dst_cloud, info):
        self.copy_data_via_ssh(src_cloud,
                               dst_cloud,
                               info,
                               utl.DIFF_BODY,
                               utl.COMPUTE_RESOURCE,
                               utl.INSTANCES_TYPE)

    def copy_ephemeral(self, src_cloud, dst_cloud, info):
        dst_storage = dst_cloud.resources[utl.COMPUTE_RESOURCE]
        src_compute = src_cloud.resources[utl.COMPUTE_RESOURCE]
        src_backend = src_compute.config.compute.backend
        dst_backend = dst_storage.config.compute.backend
        if (src_backend == CEPH) and (dst_backend == ISCSI):
            self.copy_ephemeral_ceph_to_iscsi(src_cloud, dst_cloud, info)
        elif (src_backend == ISCSI) and (dst_backend == CEPH):
            self.copy_ephemeral_iscsi_to_ceph(src_cloud, info)
        else:
            self.copy_data_via_ssh(src_cloud,
                                   dst_cloud,
                                   info,
                                   utl.EPHEMERAL_BODY,
                                   utl.COMPUTE_RESOURCE,
                                   utl.INSTANCES_TYPE)

    def copy_ephemeral_ceph_to_iscsi(self, src_cloud, dst_cloud, info):
        instances = info[utl.INSTANCES_TYPE]
        qemu_img_dst = dst_cloud.qemu_img
        qemu_img_src = src_cloud.qemu_img
        temp_src = src_cloud.cloud_config.cloud.temp
        host_dst = dst_cloud.getIpSsh()
        transporter = task_transfer.TaskTransfer(
            self.init,
            TRANSPORTER_MAP[ISCSI][ISCSI],
            resource_name=utl.INSTANCES_TYPE,
            resource_root_name=utl.EPHEMERAL_BODY)

        temp_path_src = temp_src+"/%s"+utl.DISK_EPHEM
        for inst_id, inst in instances.iteritems():
            path_src_id_temp = temp_path_src % inst_id
            host_compute_dst = inst[EPHEMERAL][HOST_DST]
            backing_file = qemu_img_dst.detect_backing_file(inst[EPHEMERAL][PATH_DST], host_compute_dst)
            self.delete_remote_file_on_compute(inst[EPHEMERAL][PATH_DST], host_dst, host_compute_dst)
            qemu_img_src.convert(utl.QCOW2, 'rbd:%s' % inst[EPHEMERAL][PATH_SRC], path_src_id_temp)
            inst[EPHEMERAL][PATH_SRC] = path_src_id_temp
            transporter.run(info=info)
            qemu_img_dst.diff_rebase(backing_file, inst[EPHEMERAL][PATH_DST], host_compute_dst)

    def copy_ephemeral_iscsi_to_ceph(self, src_cloud, info):
        instances = info[utl.INSTANCES_TYPE]
        qemu_img_src = src_cloud.qemu_img
        transporter = task_transfer.TaskTransfer(
            self.init,
            TRANSPORTER_MAP[ISCSI][CEPH],
            resource_name=utl.INSTANCES_TYPE,
            resource_root_name=utl.EPHEMERAL_BODY)

        for inst_id, inst in instances.iteritems():
            path_src = inst[EPHEMERAL][PATH_SRC]
            path_src_temp_raw = path_src + "." + utl.RAW

            host_src = inst[EPHEMERAL][HOST_SRC]
            qemu_img_src.convert(utl.RAW, path_src, path_src_temp_raw, host_src)
            inst[EPHEMERAL][PATH_SRC] = path_src_temp_raw
            transporter.run(info=info)

    def transport_from_src_to_dst(self, info):
        transporter = task_transfer.TaskTransfer(
            self.init,
            TRANSPORTER_MAP[ISCSI][ISCSI],
            resource_name=utl.INSTANCES_TYPE,
            resource_root_name=utl.DIFF_BODY)

        transporter.run(info=info)

    def transport_diff_and_merge(self, dst_cloud, info, instance_id):
        image_id = info[INSTANCES][instance_id][utl.INSTANCE_BODY]['image_id']
        cloud_cfg_dst = dst_cloud.cloud_config.cloud
        temp_dir_dst = cloud_cfg_dst.temp
        host_dst = cloud_cfg_dst.host

        base_file = "%s/%s" % (temp_dir_dst, "temp%s_base" % instance_id)
        diff_file = "%s/%s" % (temp_dir_dst, "temp%s" % instance_id)

        info[INSTANCES][instance_id][DIFF][PATH_DST] = diff_file
        info[INSTANCES][instance_id][DIFF][HOST_DST] = dst_cloud.getIpSsh()

        image_res = dst_cloud.resources[utl.IMAGE_RESOURCE]

        images = image_res.read_info(image_id=image_id)
        image = images[utl.IMAGE_RESOURCE][utl.IMAGES_TYPE][image_id]
        disk_format = image[utl.IMAGE_BODY]['disk_format']

        self.convert_image_to_file('dst_cloud', image_id, base_file)

        self.transport_from_src_to_dst(info)

        self.merge_file(dst_cloud, base_file, diff_file)
        if image_res.config.image.convert_to_raw:
            if disk_format.lower() != utl.RAW:
                self.convert_file_to_raw(host_dst, disk_format, base_file)
                disk_format = utl.RAW

        dst_image_id = self.convert_file_to_image('dst_cloud', base_file, disk_format, instance_id)

        info[INSTANCES][instance_id][INSTANCE_BODY]['image_id'] = dst_image_id

    def convert_file_to_image(self, dst_cloud, base_file, disk_format, instance_id):
        converter = convert_file_to_image.ConvertFileToImage(self.init, cloud=dst_cloud)
        dst_image_id = converter.run(file_path=base_file,
                                     image_format=disk_format,
                                     image_name="%s-image" % instance_id)
        return dst_image_id

    def convert_image_to_file(self, cloud, image_id, filename):
        convertor = convert_image_to_file.ConvertImageToFile(self.init, cloud=cloud)
        convertor.run(image_id=image_id,
                      base_filename=filename)

    def merge_file(self, cloud, base_file, diff_file):
        host = cloud.cloud_config.cloud.host
        self.rebase_diff_file(host, base_file, diff_file)
        self.commit_diff_file(host, diff_file)

    def transport_image(self, dst_cloud, info, instance_id):
        cloud_cfg_dst = dst_cloud.cloud_config.cloud
        temp_dir_dst = cloud_cfg_dst.temp
        transporter = task_transfer.TaskTransfer(
            self.init,
            TRANSPORTER_MAP[CEPH][ISCSI],
            resource_name=utl.INSTANCES_TYPE,
            resource_root_name=utl.DIFF_BODY)

        path_dst = "%s/%s" % (temp_dir_dst, "temp%s" % instance_id)
        info[INSTANCES][instance_id][DIFF][PATH_DST] = path_dst
        info[INSTANCES][instance_id][DIFF][HOST_DST] = dst_cloud.getIpSsh()
        transporter.run(info=info)
        converter = convert_file_to_image.ConvertFileToImage(dst_cloud)
        dst_image_id = converter.run(file_path=path_dst,
                                     image_format='raw',
                                     image_name="%s-image" % instance_id)
        info[INSTANCES][instance_id][INSTANCE_BODY]['image_id'] = dst_image_id

    def convert_file_to_raw(self, host, disk_format, filepath):
        with settings(host_string=host):
            with forward_agent(env.key_filename):
                run("qemu-img convert -f %s -O raw %s %s.tmp" %
                    (disk_format, filepath, filepath))
                run("mv -f %s.tmp %s" % (filepath, filepath))

    def rebase_diff_file(self, host, base_file, diff_file):
        cmd = "qemu-img rebase -u -b %s %s" % (base_file, diff_file)
        with settings(host_string=host):
            run(cmd)

    def commit_diff_file(self, host, diff_file):
        with settings(host_string=host):
            run("qemu-img commit %s" % diff_file)        
