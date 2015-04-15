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

from condensation import cloud
from condensation import utils
from cloudferrylib.utils import utils as utl
LOG = utl.get_log(__name__)


def process():
    """
    This function is entry point of this Program. We need to read files,
    create Cloud object and run migration process recursively
    """
    # read files with nova data and node data
    LOG.info("started creating schedule for node condensation")
    cloud.Cloud.from_dicts('source', *utils.read_initial_state()
                           ).migrate_to(cloud.Cloud('destination'))


if __name__ == "__main__":
    process()
