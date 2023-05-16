#!/bin/bash
SCRIPT="/etc/opt/gradingToDB.py"
if [ ! -f "$SCRIPT"  ]; then
    #Download the shutdown grading script from bucket
    gsutil cp gs://shutdown-grading/gradingToDB.py $SCRIPT
fi
# make the script executable
chmod +x $SCRIPT
# run the script
/usr/bin/python3 $SCRIPT
# delete the script
rm $SCRIPT