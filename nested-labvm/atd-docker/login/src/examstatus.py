import sys

statusFilePath = r'/home/arista/apps/examstatus/examstatus.txt'
def read_status():
    """Reads the exam status from examstatus.txt."""
    try:
        with open(statusFilePath, "r") as file:
            return file.read().strip()
    except Exception:
        return False

def write_status(status):
    """Writes the exam status (True/False) to examstatus.txt."""
    try:
        with open(statusFilePath, "w") as file:
            file.write("startExamButtonNotNeeded")
        return True
    except Exception:
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python examstatus.py <status | status=true | status=false>")
        sys.exit(1)
    
    arg = sys.argv[1]
    
    if arg == "presentstatus":
        print(read_status())
    elif arg == "status=startExamButtonNotNeeded":
        print(write_status(False))
    else:
        #print("Invalid argument. Use 'status', 'status=true', or 'status=false'.")
        print('False')
