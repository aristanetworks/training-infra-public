import time
import threading
import sys
import os
import re
import yaml
import subprocess

labACCESS = '/etc/atd/ACCESS_INFO.yaml'


def get_exam_duration(item):
    print(item)
    match = re.search(r"-ex-[A-Za-z0-9]{4}(-[0-9]-[A-Za-z0-9])", item)
    
    if not match:
        print("Not a valid exam item.")
        return None

    exam_code = match.group(1)  # Extracts the '-X-X' part

    # Determine duration based on last three characters
    duration_map = {
        "-1-2": 120,   # 120 minutes (2 hours)
        "-2-2": 240,   # 240 minutes (4 hours)
        "-3-d": 240,   # 240 minutes (4 hours)
        "-5-3": 240,   # 240 minutes (4 hours)
        "-4-1": 300    # 300 minutes (5 hours)
    }

    duration = duration_map.get(exam_code)

    if duration is None:
        print("Unknown exam duration.")
        return None

    return duration

def submit_exam():
    subprocess.run(["python","/usr/local/bin/upload_exam_unattended.py"])
    
    
def get_lab_details():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['name']
    
def save_remaining_time(exam_duration):
    with open(".remaining_time.txt", "w") as f:
        f.write(str(exam_duration["time"]))

def load_remaining_time():
    if os.path.exists(".remaining_time.txt"):
        try:
            with open(".remaining_time.txt", "r") as f:
                time_left = int(f.read().strip())
                return max(time_left, 0)
        except ValueError:
            return get_exam_duration(get_lab_details()) * 60
    return get_exam_duration(get_lab_details()) * 60

def countdown_timer(exam_duration, stop_event):
    while exam_duration["time"] > 0 and not stop_event.is_set():
        time.sleep(1)
        exam_duration["time"] -= 1
        save_remaining_time(exam_duration)
    
    if exam_duration["time"] <= 0:
        print("\nTime's up!")
        submit_exam()

def add_time(exam_duration, extra_time):
    exam_duration["time"] += extra_time
    save_remaining_time(exam_duration)
    print(f"\nAdded {extra_time // 60} minutes to the exam.")

def remove_all_time(exam_duration, stop_event):
    exam_duration["time"] = 0
    save_remaining_time(exam_duration)
    stop_event.set()
    print("\nTime removed. Submitting exam...")
    submit_exam()

def user_input_handler(exam_duration, stop_event):
    while True:
        command = input("Enter 'add' to add time, 'time' to check time, 'remove' to submit exam, or 'quit' to exit: ").strip().lower()
        
        if command == 'add':
            try:
                extra_time = int(input("Enter the number of minutes to add: ")) * 60
                add_time(exam_duration, extra_time)
            except ValueError:
                print("\nInvalid input. Please enter a number.")
        elif command == 'time':
            mins, secs = divmod(exam_duration["time"], 60)
            print(f"\nTime remaining: {mins:02d}:{secs:02d}")
        elif command == 'remove':
            remove_all_time(exam_duration, stop_event)
            break
        elif command == 'quit':
            print("\nExiting exam timer.")
            break
        else:
            print("\nUnknown command. Use 'add', 'time', 'remove', or 'quit'.")

if __name__ == "__main__":
    exam_duration = {"time": load_remaining_time()}  # Load remaining time from file
    stop_event = threading.Event()

    # Start the countdown timer in a separate thread
    timer_thread = threading.Thread(target=countdown_timer, args=(exam_duration, stop_event))
    timer_thread.start()

    # Handle user input in the main thread
    #user_input_handler(exam_duration, stop_event)

    # Wait for the timer thread to finish
    timer_thread.join()