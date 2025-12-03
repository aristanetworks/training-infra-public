#!/usr/bin/env python
import os
import sys
import re
import syslog
from ruamel.yaml import YAML
from ConfigureTopology.ConfigureTopology import ConfigureTopology
from cloud_logging_utils import setup_cloud_logging

# Initialize cloud logging
logger = setup_cloud_logging('login')



######################################
########## Global Variables ##########
######################################
__version__ = "2.1"

# Open ACCESS_INFO.yaml and load the variables
f = open('/etc/atd/ACCESS_INFO.yaml')
access_info = YAML().load(f)
f.close()

# Set Main Script Variables
topology = access_info['topology']
login_info = access_info['login_info']

# Open topo_build.yaml and load
try:
  f = open('/opt/atd/topologies/{0}/topo_build.yml'.format(topology))
  topoinfo = YAML().load(f)
  f.close()
except:
  sys.exit("topo_build not available")

veos_info = topoinfo['nodes']
additional_ssh_nodes = topoinfo['additional_ssh_nodes']

# Set default menu mode
menu_mode = 'MAIN'
previous_menu = ''

###################################################
#################### Functions ####################
###################################################

def text_to_int(text):
  return int(text) if text.isdigit() else text

def natural_keys(text):
  return [ text_to_int(char) for char in re.split(r'(\d+)', text) ]

def sort_veos(vd):
  tmp_l = []
  tmp_d = {}
  fin_l = []
  for t_veos in vd:
        t_veos_name = list(t_veos.keys())[0]
        tmp_l.append(t_veos_name)
        tmp_d[t_veos_name] = dict(t_veos[t_veos_name])
        tmp_d[t_veos_name]['hostname'] = t_veos_name
  tmp_l.sort(key=natural_keys)
  # If cvx in list, move to end
  if 'cvx' in tmp_l[0]:
        tmp_cvx = tmp_l[0]
        tmp_l.pop(0)
        tmp_l.append(tmp_cvx)
  for t_veos in tmp_l:
    fin_l.append(tmp_d[t_veos])
  return(fin_l)

def get_device_name_for_logging(user_input, device_dict, veos_info_sorted):
    """
    Helper function to get device name for logging purposes

    Parameters:
    user_input = User's menu selection
    device_dict = Dictionary mapping selections to device IPs
    veos_info_sorted = Sorted list of device information

    Returns:
    device_name = The hostname of the device, or 'unknown' if not found
    """
    # Check if user input is directly a hostname
    if user_input.lower() in [v['hostname'] for v in veos_info_sorted]:
        return user_input.lower()

    # Try to find hostname by matching IP address
    selected_ip = device_dict.get(user_input, '')
    for device in veos_info_sorted:
        if device['ip_addr'] == selected_ip:
            return device['hostname']

    # Default to unknown if not found
    return 'unknown'

def send_to_syslog(mstat,mtype):
    """
    Function to send output from service file to Syslog
    Parameters:
    mstat = Message Status, ie "OK", "INFO" (required)
    mtype = Message to be sent/displayed (required)
    """
    mmes = "\t" + mtype
    print("[{0}] {1}".format(mstat,mmes.expandtabs(7 - len(mstat))))
    # Also log to cloud logging
    if mstat == 'ERROR':
        logger.error(mtype, extra={'labels': {'status': mstat}})
    elif mstat == 'INFO':
        logger.info(mtype, extra={'labels': {'status': mstat}})
    else:
        logger.info(f"[{mstat}] {mtype}", extra={'labels': {'status': mstat}})


def device_menu():
    global menu_mode
    global previous_menu
    logger.info("User entered Device SSH menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'menu_enter'}})
    os.system("clear")
    # Create Device Dict to save devices and later execute based on matching the counter to a dict key
    device_dict = {}

    # Sort veos instances
    veos_info_sorted = sort_veos(veos_info)
    print("\n\n*******************************************")
    print("*****Jump Host for Arista Training Labs****")
    print("*******************************************")
    print("\n\n==========Device SSH Menu==========\n")
    print("Screen Instructions:\n")

    print("* Select specific screen - Ctrl + a <number>")
    print("* Select previous screen - Ctrl + a p")
    print("* Select next screen - Ctrl + a n")
    print("* Exit all screens (return to menu) - Ctrl + a \\")

    print("\nPlease select from the following options:")

    counter = 1
    for veos in veos_info_sorted:
        print("{0}. {1} ({2})".format(str(counter),veos['hostname'],veos['hostname']))
        device_dict[str(counter)] = veos['ip_addr']
        device_dict[veos['hostname']] = veos['ip_addr']
        counter += 1
    
    print("\nOther Options: ")
    print("95. Upload your exam (exam)")
    print("96. Screen (screen) - Opens a screen session to each of the hosts")
    print("97. Back to Previous Menu (back)")
    print("98. Shell (shell/bash)")
    print("99. Back to Main Menu (main/exit) - CTRL + c")
    print("")
    user_input = input("What would you like to do? ").replace(' ', '')

    # Check to see if input is in device_dict
    counter = 1
    try:
      if user_input.lower() in device_dict:
          # Find the device name for logging
          device_name = get_device_name_for_logging(user_input, device_dict, veos_info_sorted)
          logger.info(f"User selected SSH to device", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'ssh_device', 'device': device_name, 'selection': user_input}})
          os.system('ssh -o StrictHostKeyChecking=no ' + device_dict[user_input])
          logger.info(f"User returned from SSH session", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'ssh_return', 'device': device_name}})
      elif user_input == '96' or user_input.lower() == 'screen':
          logger.info("User selected screen option", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'screen', 'option': '96'}})
          os.system('/usr/bin/screen')
          logger.info("User returned from screen session", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'screen_return'}})
      elif user_input == '95' or user_input.lower() == 'exam':
        logger.info("USER STARTED EXAM UPLOAD - Option 95", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'exam_upload_start', 'option': '95', 'critical': 'true'}})
        os.system('upload_exam_unattended.py')
        logger.info("User returned from exam upload", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'exam_upload_return', 'option': '95'}})
      elif user_input == '97' or user_input.lower() == 'back':
          logger.info("User selected back to previous menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'navigate_back', 'option': '97', 'previous_menu': previous_menu}})
          if menu_mode == previous_menu:
              menu_mode = 'MAIN'
          else:
              menu_mode = previous_menu
      elif user_input == '98' or user_input.lower() == 'bash' or user_input.lower() == 'shell':
          logger.info("User selected shell/bash", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'shell', 'option': '98'}})
          os.system('/bin/bash')
          logger.info("User returned from shell", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'shell_return'}})
      elif user_input == '99' or user_input.lower() == 'main' or user_input == '99' or user_input.lower() == 'exit':
          logger.info("User selected back to main menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'navigate_main', 'option': '99'}})
          menu_mode = 'MAIN'
      else:
          logger.warning(f"Invalid input in Device SSH menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'invalid_input', 'input': user_input}})
          print("Invalid Input")
    except KeyboardInterrupt:
        print('Stopped due to keyboard interrupt.')
        send_to_syslog('ERROR', 'Keyboard interrupt.')
        logger.warning("Keyboard interrupt in Device SSH menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'keyboard_interrupt'}})
    except:
        logger.error("Error in Device SSH menu", extra={'labels': {'menu': 'DEVICE_SSH', 'action': 'error', 'input': user_input}})
        print("Invalid Input")



def lab_options_menu():
    global menu_mode
    global previous_menu

    logger.info(f"User entered Lab Options menu", extra={'labels': {'menu': menu_mode, 'action': 'menu_enter'}})
    os.system("clear")
    print("\n\n*******************************************")
    print("*****Jump Host for Arista Training Labs****")
    print("*******************************************")

    if menu_mode == 'LAB_OPTIONS':
      # Get Yaml Files in /home/arista/menus
      menu_files = os.listdir('/home/arista/menus')
      menu_files.sort()
      
    # Create Lab Options dict to save lab and later navigate to that menu of labs
      lab_options_dict = {}

      # Display Lab Options
      counter = 1
      print('\n\n==========Lab Options Menu==========\n')
      print("Please select from the following options: \n")
      
      # Iterate through lab menu files and print names without .yaml - Increment counter to reflect choices
      counter = 1
      for menu_type in menu_files:
          if menu_type != 'default.yaml':
            # Print Lab Menu and add options to lab options dict
            print('{0}. {1} ({2})'.format(str(counter),menu_type.replace('-', ' ').replace('.yaml', ''), menu_type.replace('.yaml', '').lower() ))
            lab_options_dict[str(counter)] = menu_type
            lab_options_dict[menu_type.replace('.yaml', '').lower()] = menu_type
            counter += 1

      # Additional Menu Options
      print("\nOther Options: ")
      print("97. Back to Previous Menu (back)")
      print("98. SSH to Devices (ssh)")
      print("99. Back to Main Menu (main/exit) - CTRL + c\n")
      
      user_input = input("\nWhat would you like to do?: ").replace(' ', '')

      # Check to see if digit is in lab_options dict
      try:
          if user_input.lower() in lab_options_dict:
              selected_lab_menu = lab_options_dict[user_input]
              logger.info(f"User selected lab menu type", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'select_lab_menu', 'lab_menu': selected_lab_menu, 'selection': user_input}})
              previous_menu = menu_mode
              menu_mode = 'LAB_' + selected_lab_menu
          elif user_input == '97' or user_input.lower() == 'back':
              logger.info("User navigated back from lab options", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'navigate_back', 'option': '97'}})
              if menu_mode == previous_menu:
                  menu_mode = 'MAIN'
              else:
                  menu_mode = previous_menu
          elif user_input == '98' or user_input.lower() == 'ssh':
              logger.info("User navigated to SSH menu from lab options", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'navigate_ssh', 'option': '98'}})
              previous_menu = menu_mode
              menu_mode = 'DEVICE_SSH'
          elif user_input == '99' or user_input.lower() == 'main' or user_input == '99' or user_input.lower() == 'exit':
              logger.info("User navigated to main menu from lab options", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'navigate_main', 'option': '99'}})
              menu_mode = 'MAIN'
          else:
              logger.warning(f"Invalid input in lab options menu", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'invalid_input', 'input': user_input}})
              print("Invalid Input")
      except KeyboardInterrupt:
          print('Stopped due to keyboard interrupt.')
          send_to_syslog('ERROR', 'Keyboard interrupt.')
          logger.warning("Keyboard interrupt in lab options menu", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'keyboard_interrupt'}})
      except:
          logger.error("Error in lab options menu", extra={'labels': {'menu': 'LAB_OPTIONS', 'action': 'error', 'input': user_input}})
          print("Invalid Input")



    elif 'LAB_' in menu_mode and menu_mode != 'LAB_OPTIONS':
      
      # Create Commands dict to save commands and later execute based on matching the counter to a dict key
      options_dict = {}

      # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
      menu_file = open('/home/arista/menus/' + menu_mode[4:])
      menu_info = YAML().load(menu_file)
      menu_file.close()

      print('\n\n==========Lab Options Menu - {0}==========\n'.format(menu_mode[4:].replace('-', ' ').replace('.yaml', '')))
      print("Please select from the following options: \n")
      
      counter = 1
      for lab in menu_info['lab_list']:
        print("{0}. {1}".format(str(counter),menu_info['lab_list'][lab]['description']))
        options_dict[str(counter)] = {'selected_lab': lab, 'selected_menu': menu_mode[4:].replace('.yaml', '')}
        options_dict[lab] = {'selected_lab': lab, 'selected_menu': menu_mode[4:].replace('.yaml', '')}
        counter += 1
      print('\n')

      # Additional Menu Options
      print("Other Options: ")
      print("97. Back to Previous Menu (back)")
      print("98. SSH to Devices (ssh)")
      print("99. Back to Main Menu (main/exit) - CTRL + c\n")

      # User Input
      user_input = input("What would you like to do?: ").replace(' ', '')

      # Check to see if input is in commands_dict
      try:
          if user_input.lower() in options_dict:
              selected_menu = options_dict[user_input]['selected_menu']
              selected_lab = options_dict[user_input]['selected_lab']
              logger.info(f"User selected lab to deploy", extra={'labels': {'menu': menu_mode, 'action': 'deploy_lab', 'lab_menu': selected_menu, 'lab': selected_lab, 'selection': user_input}})
              previous_menu = menu_mode
              ConfigureTopology(selected_menu=selected_menu, selected_lab=selected_lab)
              logger.info(f"Lab deployment completed", extra={'labels': {'menu': menu_mode, 'action': 'deploy_complete', 'lab_menu': selected_menu, 'lab': selected_lab}})
          elif user_input == '97' or user_input.lower() == 'back':
              logger.info("User navigated back from lab menu", extra={'labels': {'menu': menu_mode, 'action': 'navigate_back', 'option': '97'}})
              if menu_mode == previous_menu:
                  menu_mode = 'MAIN'
              else:
                  menu_mode = previous_menu
          elif user_input == '98' or user_input.lower() == 'ssh':
              logger.info("User navigated to SSH from lab menu", extra={'labels': {'menu': menu_mode, 'action': 'navigate_ssh', 'option': '98'}})
              previous_menu = menu_mode
              menu_mode = 'DEVICE_SSH'
          elif user_input == '99' or user_input.lower() == 'main' or user_input == '99' or user_input.lower() == 'exit':
              logger.info("User navigated to main from lab menu", extra={'labels': {'menu': menu_mode, 'action': 'navigate_main', 'option': '99'}})
              menu_mode = 'MAIN'
          else:
              logger.warning(f"Invalid input in lab menu", extra={'labels': {'menu': menu_mode, 'action': 'invalid_input', 'input': user_input}})
              print("Invalid Input")
      except KeyboardInterrupt:
          print('Stopped due to keyboard interrupt.')
          send_to_syslog('ERROR', 'Keyboard interrupt.')
          logger.warning("Keyboard interrupt in lab menu", extra={'labels': {'menu': menu_mode, 'action': 'keyboard_interrupt'}})
      except:
          logger.error("Error in lab menu", extra={'labels': {'menu': menu_mode, 'action': 'error', 'input': user_input}})
          print("Invalid Input")

def main_menu():
    global menu_mode
    global previous_menu

    logger.info("User entered Main menu", extra={'labels': {'menu': 'MAIN', 'action': 'menu_enter'}})
    os.system("clear")
    print("\n\n*******************************************")
    print("*****Jump Host for Arista Training Labs****")
    print("*******************************************")
    print("\n\n==========Main Menu==========\n")
    print("Please select from the following options: ")

    # Create options dict to later send to deploy_lab
    options_dict = {}

    # Open yaml for the default yaml and read what file to lookup for default menu
    default_menu_file = open('/home/arista/menus/default.yaml')
    default_menu_info = YAML().load(default_menu_file)
    default_menu_file.close()

    # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
    try:
      menu_file = open('/home/arista/menus/{0}'.format(default_menu_info['default_menu']))
      menu_info = YAML().load(menu_file)
      menu_file.close()
    except:
      print("Exiting menu")
      quit()


    
    counter = 1
    menu = default_menu_info['default_menu'].replace('.yaml', '')
    for lab in menu_info['lab_list']:
      print("{0}. {1}".format(str(counter),menu_info['lab_list'][lab]['description']))
      options_dict[str(counter)] = {'selected_lab': lab, 'selected_menu': menu}
      options_dict[lab] = {'selected_lab': lab, 'selected_menu': menu}
      counter += 1
    print('\n')



    print("97. Additional Labs (labs)")
    print("98. SSH to Devices (ssh)")
    print("99. Exit LabVM (quit/exit) - CTRL + c")
    print("")

    user_input = input("What would you like to do?: ").replace(' ', '')
    
    # Check user input to see which menu to change to
    try:
      if user_input.lower() in options_dict:
          selected_menu = options_dict[user_input]['selected_menu']
          selected_lab = options_dict[user_input]['selected_lab']
          logger.info(f"User selected lab from main menu", extra={'labels': {'menu': 'MAIN', 'action': 'deploy_lab', 'lab_menu': selected_menu, 'lab': selected_lab, 'selection': user_input}})
          ConfigureTopology(selected_menu=selected_menu, selected_lab=selected_lab)
          logger.info(f"Lab deployment completed from main menu", extra={'labels': {'menu': 'MAIN', 'action': 'deploy_complete', 'lab_menu': selected_menu, 'lab': selected_lab}})
      elif user_input == '98' or user_input.lower() == 'ssh':
        logger.info("User navigated to SSH from main menu", extra={'labels': {'menu': 'MAIN', 'action': 'navigate_ssh', 'option': '98'}})
        previous_menu = menu_mode
        menu_mode = 'DEVICE_SSH'
      elif user_input == '97' or user_input.lower() == 'labs':
        logger.info("User navigated to additional labs", extra={'labels': {'menu': 'MAIN', 'action': 'navigate_labs', 'option': '97'}})
        previous_menu = menu_mode
        menu_mode = 'LAB_OPTIONS'
      elif user_input == '99' or user_input.lower() == 'exit' or user_input.lower() == 'quit':
        logger.info("User exiting from main menu", extra={'labels': {'menu': 'MAIN', 'action': 'exit', 'option': '99'}})
        menu_mode = 'EXIT'
      else:
        logger.warning(f"Invalid input in main menu", extra={'labels': {'menu': 'MAIN', 'action': 'invalid_input', 'input': user_input}})
        print("Invalid Input")
    except KeyboardInterrupt:
        print('Stopped due to keyboard interrupt.')
        send_to_syslog('ERROR', 'Keyboard interrupt.')
        logger.warning("Keyboard interrupt in main menu", extra={'labels': {'menu': 'MAIN', 'action': 'keyboard_interrupt'}})
    except:
        logger.error("Error in main menu", extra={'labels': {'menu': 'MAIN', 'action': 'error', 'input': user_input}})
        print("Invalid Input")




##############################################
#################### Main ####################
##############################################

def main():
    if os.getuid() != 0:
        logger.info("Login session started", extra={'labels': {'user': 'arista', 'operation': 'login'}})
        global menu_mode
        # Create Menu Manager
        with open('/home/arista/menus/default.yaml') as fdefault:
            topo_default = YAML().load(fdefault.read())
        if topo_default['default_menu'] == 'ssh':
            menu_mode = 'DEVICE_SSH'
            logger.info("Default menu set to SSH", extra={'labels': {'menu': 'ssh'}})
        if sys.stdout.isatty():
            while menu_mode:
                try:
                    if menu_mode == 'MAIN':
                      main_menu()
                    elif menu_mode == 'DEVICE_SSH':
                      device_menu()
                    elif 'LAB_' in menu_mode:
                      lab_options_menu()
                    elif menu_mode == 'EXIT':
                      logger.info("User session ended - EXIT selected", extra={'labels': {'action': 'session_end', 'exit_type': 'normal'}})
                      print('User exited.')
                      quit()
                except KeyboardInterrupt:
                    send_to_syslog('INFO', 'Script fully exited due to keyboard interrupt.')
                    if menu_mode == 'MAIN':
                      logger.info("User session ended - keyboard interrupt", extra={'labels': {'action': 'session_end', 'exit_type': 'keyboard_interrupt'}})
                      print('User exited.')
                      quit()
                    else:
                      logger.info("Keyboard interrupt - returning to main menu", extra={'labels': {'menu': menu_mode, 'action': 'interrupt_to_main'}})
                      menu_mode = 'MAIN'

        else:
            os.system("/usr/lib/openssh/sftp-server")
    
    else:

      os.system("/bin/bash")
        
if __name__ == '__main__':
    main()
