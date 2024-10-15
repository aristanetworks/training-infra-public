import subprocess
import sys

# Placeholder for the list of network interfaces
interfaces = ["RP11x5", "RP11x6", "RP11x7", "RP11x8", "RP11x9"]  # Add your interfaces here

def check_tc_exists(interface):
    result = subprocess.run(f"tc qdisc show dev {interface}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return "htb" in result.stdout

def enable_tc(interface, delay=3000):
    if check_tc_exists(interface):
        print(f"Traffic control already enabled on {interface}")
        return

    commands = [
        f"sudo tc qdisc add dev {interface} root handle 1: htb default 12",
        f"sudo tc class add dev {interface} parent 1:1 classid 1:12 htb rate 1000mbit",
        f"sudo tc qdisc add dev {interface} parent 1:12 handle 10: netem delay {delay}ms"
    ]
    for cmd in commands:
        subprocess.run(cmd, shell=True, check=True)
    print(f"Enabled traffic control on {interface} with delay {delay}ms")

def disable_tc(interface):
    if not check_tc_exists(interface):
        print(f"No traffic control configuration found on {interface}")
        return

    cmd = f"sudo tc qdisc del dev {interface} root"
    subprocess.run(cmd, shell=True, check=True)
    print(f"Disabled traffic control on {interface}")

def show_tc(interface):
    result = subprocess.run(f"tc qdisc show dev {interface}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    print(f"Traffic control configuration for {interface}:\n{result.stdout}")

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ["ENABLE", "DISABLE", "SHOW"]:
        print("Usage: python script.py [ENABLE|DISABLE|SHOW] [-d DELAY] [-i INTERFACES]")
        sys.exit(1)

    action = sys.argv[1]
    delay = 3000  # Default delay value

    if "-d" in sys.argv:
        try:
            delay_index = sys.argv.index("-d") + 1
            delay = int(sys.argv[delay_index])
        except (ValueError, IndexError):
            print("Invalid delay value. Please provide an integer value for delay.")
            sys.exit(1)

    if "-i" in sys.argv:
        try:
            interfaces_index = sys.argv.index("-i") + 1
            interfaces = sys.argv[interfaces_index].split(',')
        except IndexError:
            print("Invalid interfaces value. Please provide a comma-separated list of interfaces.")
            sys.exit(1)

    for interface in interfaces:
        if action == "ENABLE":
            enable_tc(interface, delay)
        elif action == "DISABLE":
            disable_tc(interface)
        elif action == "SHOW":
            show_tc(interface)