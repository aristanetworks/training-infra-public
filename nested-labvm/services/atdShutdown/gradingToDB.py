#! /usr/bin/python3
import yaml
import requests
import datetime
import subprocess
import sys
import json
import logging

class GradingToDB:


    _RESULTS_FILE = "/etc/opt/graderlogs/results.yaml"
    _LOADED_LABS = "/home/arista/arista-dir/loaded_labs.yaml"

    _ACCESS_INFO = "/etc/atd/ACCESS_INFO.yaml"
    _TITLE_MAPPINGS = {
        "Training Level 3 Lab":"training-3-1",
    }
    _LOG_FILE = "/etc/opt/shutdown-grading.log"
    _EDIT_INSTANCE = "https://us-central1-{0}.cloudfunctions.net/edit-instance"

    def __init__(self) -> None:
        self.logger = logging.getLogger("shutdowngrading")
        self.logger.setLevel(logging.DEBUG)
        file_handler = logging.FileHandler(self._LOG_FILE)
        formatter = logging.Formatter("%(asctime)s - %(levelname)-8s - {}%(message)s{}")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def open_yaml(self,file_path):
        """
        Gets the lab name and returns the relative lab code
        """
        try:
            with open(file_path, 'r') as file:
                data = yaml.safe_load(file)
            return data
        except Exception as e:
            self.logger.debug(e)

    def run_in_container(self,container_id,arguments):
        """
        Takes the container id and runs the arguments in the container
        """
        command = ['docker','exec',container_id] + arguments
        try:
            #docker_out = subprocess.run(["docker","ps",],stdout=subprocess.PIPE)
            output = subprocess.run(command, stdout=subprocess.PIPE)
            self.logger.debug(f"Container:{container_id} command:{command}")
            #self.logger.debug(f"Docker ps {docker_out}")
            self.logger.debug(f"Output: {output}")
            return True
        except Exception as e:
            self.logger.debug(e)
            return False
    
    def add_data_todb(self,url_endpoint,data):
        """
        Call the edit CF to update the data in DB
        """
        #add data
        #response = requests.get(url=url_endpoint)
        response = requests.post(url=url_endpoint,data=json.dumps(data,default=str))
        try:
            self.logger.debug("Response from edit-instance CF: {}".format(response.json()))
            return(response.json())
        except Exception as e:
            self.logger.debug("Exception when calling the edit-instance CF: {}".format(e))
            return(False)

    def combine_and_sort(self,grading_data,loaded_data):
        """
        Takes the grading results and loaded labs results
        Sorts them according to the timestamps
        Combines them and returns the combined output
        """
        #first combine the data together
        combined_data = []
        if loaded_data:
            for event in loaded_data['loaded_labs']:
                combined_data.append({"event":"loaded", "timestamp":datetime.datetime.strptime(event["loaded_timestamp"], "%Y_%m_%d-%H:%M:%S"), "lab": event['lab']})

        if grading_data:
            for event_num in range(len(grading_data['failed_labs'])):
                #The passed and failed labs will be in the same order hence relying on index
                combined_data.append({"event":"grading","timestamp":datetime.datetime.strptime(grading_data['failed_labs'][event_num]["graded_timestamp"], "%Y_%m_%d-%H:%M:%S"),"passed":grading_data['passed_labs'][event_num]['scores'], "failed":grading_data['failed_labs'][event_num]['scores']})

        #sort the combined data based on the timestamp
        combined_data.sort(key=lambda x: x.get('timestamp'))
        return combined_data



    def main(self):
        # get the access info
        self.logger.debug("Starting the execution of shutdown script")
        host_data = self.open_yaml(self._ACCESS_INFO)
        self.logger.debug("Parsed the access info data")
        if not host_data:
            self.logger.debug("No host data file. Exiting the execution")
            sys.exit()

        #call the running configs and grading image
        lab_code = self._TITLE_MAPPINGS[host_data["title"]]
        running_configs_status = self.run_in_container('atd-login',['sudo', 'localGrading.py'])
        if not running_configs_status:
            sys.exit()
        self.logger.debug("Pulled the current running configs successfully")
        grading_status = self.run_in_container('atd-grader',['python', 'selfgrader.py', lab_code])
        if not grading_status:
            sys.exit()
        self.logger.debug("Graded the labs successfully")

        # parse the data from output files and form the data to upload
        try:
            pass_fail_data = self.open_yaml(self._RESULTS_FILE)
            loaded_labs_data = self.open_yaml(self._LOADED_LABS)
        except Exception as e:
            self.logger.debug(f"Exception occured while loading the files: {e}")
            sys.exit()
        data = self.combine_and_sort(pass_fail_data,loaded_labs_data)
        if not data:
            self.logger.debug("Error while combing and sorting the data")

        # get the instance details from ACCESS file
        instance_name = host_data['name']
        zone = host_data['zone']
        project = host_data['project']

        #send instance data and the parsed data to CF
        url = self._EDIT_INSTANCE.format(project)
        endpoint = url + f"?function=gradestodb&instance={instance_name}&zone={zone}"
        response = self.add_data_todb(endpoint,data)
        if not response:
            self.logger.debug("Failure updating the database")
        else:
            self.logger.debug("Successfully updated the database")


if __name__ == "__main__":

    obj = GradingToDB()
    obj.main()