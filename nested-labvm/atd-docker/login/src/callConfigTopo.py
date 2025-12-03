from ConfigureTopology.ConfigureTopology import ConfigureTopology
import sys
from cloud_logging_utils import setup_cloud_logging

logger = setup_cloud_logging('callConfigTopo')

# Log the configuration request
selected_menu = str(sys.argv[1])
selected_lab = str(sys.argv[2])
logger.info(f"Calling ConfigureTopology: menu={selected_menu}, lab={selected_lab}", extra={'labels': {'menu': selected_menu, 'lab': selected_lab}})

ConfigureTopology(selected_menu=selected_menu,selected_lab=selected_lab,public_module_flag=True)
logger.info(f"ConfigureTopology completed", extra={'labels': {'menu': selected_menu, 'lab': selected_lab, 'status': 'completed'}})