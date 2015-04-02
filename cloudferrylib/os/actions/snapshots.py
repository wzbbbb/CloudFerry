# Copyright (c) 2015 Mirantis Inc.
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


from cloudferrylib.base.action import action
from cloudferrylib.utils.drivers import ssh_file_to_file
from cloudferrylib.utils import utils


LOG = utils.get_log(__name__)


class MysqlDump(action.Action):

    def run(self, *args, **kwargs):
        # dump mysql to file
        # probably, we have to choose what databases we have to dump
        # by default we dump all databases
        user = self.cloud.cloud_config.mysql.user
        password = self.cloud.cloud_config.mysql.password
        path = self.cloud.cloud_config.snapshot.snapshot_path
        if password == '':
            password_arg = ''
        else:
            password_arg = "--password=" + password
        command = ("mysqldump "
                   "--user={0} "
                   "{1} "
                   "--opt "
                   "--all-databases > {2}").format(user, password_arg, path)
        LOG.info("dumping database with command '%s'", command)
        self.cloud.ssh_util.execute(command)
        # copy dump file to host with cloudferry (for now just in case)
        # in future we will store snapshot for every step of migration
        context = {
            'host_src': self.cloud.cloud_config.mysql.host,
            'host_dst': self.cloud.cloud_config.snapshot.host,
            'path_src': self.cloud.cloud_config.snapshot.snapshot_path,
            'path_dst': self.cloud.cloud_config.snapshot.snapshot_path}
        LOG.info("copying {host_src}:{path_src} to "
                 "{host_dst}:{path_src}".format(**context))
        ssh_file_to_file.SSHFileToFile(self.src_cloud, self.dst_cloud,
                                       self.cloud.cloud_config).transfer(
            context)
        return {}


class MysqlRestore(action.Action):

    def run(self, *args, **kwargs):
        # apply sqldump from file to mysql
        user = self.cloud.cloud_config.mysql.user
        password = self.cloud.cloud_config.mysql.password
        path = self.cloud.cloud_config.snapshot.snapshot_path
        if password == '':
            password_arg = ''
        else:
            password_arg = "--password=" + password
        command = ("mysql "
                   "--user={0} "
                   "{1} "
                   "< {2}").format(user, password_arg, path)
        LOG.info("restoring database with command '%s'", command)
        self.cloud.ssh_util.execute(command)
        return {}
