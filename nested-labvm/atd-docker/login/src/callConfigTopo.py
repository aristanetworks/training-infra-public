from ConfigureTopology.ConfigureTopology import ConfigureTopology
import sys
#ConfigureTopology(selected_menu='training-l3',selected_lab='reset',public_module_flag=True)
ConfigureTopology(selected_menu=str(sys.argv[1]),selected_lab=str(sys.argv[2]),public_module_flag=True)