const labStaustext = document.getElementById("labStatusByApi");
labStaustext.innerHTML = "<td>Please wait, Lab Status is being loaded...</td>";
let failedSwitches = []
const checkIsAllSwitchesOK = localStorage.getItem('isAllSwitchesOk')
isAllSwitchesOk = true;
if (checkIsAllSwitchesOK == 'yes' || checkIsAllSwitchesOK == undefined) {
    isAllSwitchesOk = true;
} else {
    isAllSwitchesOk = false;
}
resetRequestSubmittedTime = localStorage.getItem('resetRequestSubmittedTime')
if (resetRequestSubmittedTime) {
    document.getElementById('resetOkMSG').innerHTML = "Please wait, reset request has been submitted at " + resetRequestSubmittedTime
}
$('#labMenu').click(function (event) {
    document.getElementById('labMenuDiv').style.display = 'block'
    document.getElementById('mainContent').style.display = 'none'
    document.getElementById('labStatusContent').style.display = 'none'
    document.getElementById('labGradingData').style.display = 'none'
    clearInterval(labStatusInterval)
});
$('#home').click(function (event) {
    document.getElementById('labMenuDiv').style.display = 'none'
    document.getElementById('mainContent').style.display = 'block'
    document.getElementById('labStatusContent').style.display = 'none'
    document.getElementById('labGradingData').style.display = 'none'
    clearInterval(labStatusInterval)
})
$('#labStaus').click(function (event) {
    document.getElementById('labMenuDiv').style.display = 'none'
    document.getElementById('mainContent').style.display = 'none'
    document.getElementById('labStatusContent').style.display = 'block'
    document.getElementById('labGradingData').style.display = 'none'
    getLabStatus()
    labStatusInterval = setInterval(
        () => {
            getLabStatus()
        }, 30000
    )
})
$('#labGrading').click(function (event) {
    document.getElementById('labMenuDiv').style.display = 'none'
    document.getElementById('mainContent').style.display = 'none'
    document.getElementById('labStatusContent').style.display = 'none'
    document.getElementById('labGradingData').style.display = 'block'
    loadData("labGrading")
    //clearInterval(labStatusInterval)
})

$('#resetLabs').click((event) => {
    if (confirm('Please click ok to reset below switches \n-' + failedSwitches.join('\n-'))) {
        document.getElementById('resetLabs').innerHTML = ''
        document.getElementById('resetOkMSG').innerHTML = "Please wait, reset request has been submitted at " + new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString()
        isAllSwitchesOk = false;
        localStorage.setItem('isAllSwitchesOk', 'no')
        localStorage.setItem('resetRequestSubmittedTime', new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString())
        $.get('/resetLab?lab_names=' + failedSwitches.join(','), (res) => {

        })
    }
})


function getLabStatus() {
    let switchesTable = document.getElementById("labStatusByApi");


    $.get('/labStaus', (res) => {
        switchesTable.innerHTML = "";
        failedSwitches = []
        res.response.forEach(
            (item) => {
                let row = document.createElement("tr");
                let labName = document.createElement("td");
                values = item.split(',')
                labName.innerHTML = values[0];
                let labStatus = document.createElement("td");
                labStatus.innerHTML = values[1];
                if (values[1].indexOf("Ok") >= 0) {
                    labStatus.style.color = "green"
                } else {
                    failedSwitches.push(values[0])
                    labStatus.style.color = "red"
                    labStatus.style.fontWeight = "bold"
                    labStatus.innerHTML = values[1];
                }
                row.appendChild(labName);
                row.appendChild(labStatus);
                switchesTable.appendChild(row);
            }
        )
        if (failedSwitches.length == 0) {
            isAllSwitchesOk = true;
            localStorage.setItem('isAllSwitchesOk', 'yes')
            document.getElementById('resetOkMSG').innerHTML = ''
            localStorage.removeItem('resetRequestSubmittedTime')
        }
        if (isAllSwitchesOk && failedSwitches.length > 0) {
            document.getElementById('resetLabs').innerHTML = 'Please <span style="color: blue; text-decoration: underline; cursor: pointer;">click here</span> to reset failed switches'

        } else {
            document.getElementById('resetLabs').innerHTML = ''
        }

        document.getElementById('lastUpdated').innerHTML = "Last updated : " + new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString()
    })

}

function gradeButtonListener(gradeButton) {
    gradeButton.addEventListener('click', () => {
        fetch('/grade', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
      })
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then(data => {
        loadGradingData(data.grading)
        document.getElementById('grade-button').disabled = false;
        displayConvertTime(data.timestamp)
      })
      .catch(error => {
        displayGradeError("No details to show")
        document.getElementById('grade-button').disabled = false;
        displayConvertTime(data.timestamp)
      });
    });
    };

function loadData(item) {
    if (item === 'labGrading') {
        // Show table and button
        document.getElementById('grade-button').style.display = 'block';
        document.getElementById('grade-button').disabled = true;
        // send request to backend
        const gradeButton = document.getElementById('grade-button')
        fetch('/grade')
            .then(response => {
            if (response.ok) {
                response.json().then(data => {
                //Add timestamp to the page
                displayConvertTime(data.timestamp)
                if (data.grading == "No data available") {
                    displayGradeError("No details to show")
                    gradeButton.disabled = false;
                    gradeButtonListener(gradeButton)
                    } else {
                    loadGradingData(data.grading)
                    gradeButton.disabled = false;
                    gradeButtonListener(gradeButton)
                    };
                });
            } else {
                response.text().then(errorMessage => {
                // Display error message and enable button
                    displayGradeError("No details to show");
                    gradeButton.disabled = false;
                    gradeButtonListener(gradeButton)
                });
            }
            });
    };
    };
function loadGradingData(data) {
    // Get reference to the parent element where the collapsible items will be added
    const parentElem = document.getElementById("grades");
    // Clear the container HTML content
    parentElem.innerHTML = '';
    // Loop through the JSON data and create collapsible items for each lab
    for (const lab in data) {
        // Create the outer lab header element
        const labHeader = document.createElement("button");
        labHeader.classList.add("collapsible");
        labHeader.textContent = lab + ' ( Misconfiguration in ' + Object.keys(data[lab]).length + ' devices)';

        // Create the inner lab content element
        const labContent = document.createElement("div");
        labContent.classList.add("content");

        // Loop through the devices for the current lab and create collapsible items for each device
        for (const device in data[lab]) {
            // Create the outer device header element
            const deviceHeader = document.createElement("button");
            deviceHeader.classList.add("collapsible");
            deviceHeader.textContent = device + ' (' + data[lab][device].length +' errors)';

            // Create the inner device content element
            const deviceContent = document.createElement("div");
            deviceContent.classList.add("content");

            // Loop through the comments for the current leaf and create a list item for each comment
            const comments = data[lab][device];
            const commentList = document.createElement("ul");
            for (const comment of comments) {
                const commentItem = document.createElement("li");
                //commentItem.textContent = comment.comment;
                if (comment.reference != "None") {
                    commentItem.textContent = comment.comment + ", refer:" + comment.reference
                } else {
                    commentItem.textContent = comment.comment;
                }
                    commentList.appendChild(commentItem);
            }

            // Add the comment list to the device content element
            deviceContent.appendChild(commentList);

            // Add the device header and content to the inner lab content element
            labContent.appendChild(deviceHeader);
            labContent.appendChild(deviceContent);

            // Add a click listener to the leaf header to toggle the leaf content
            deviceHeader.addEventListener("click", () => {
            deviceContent.classList.toggle("active");
            if (deviceContent.style.maxHeight) {
                deviceContent.style.maxHeight = null;
            } else {
                deviceContent.style.maxHeight = deviceContent.scrollHeight + "px";
            }
            });
        }

        // Add the lab header and content to the parent element
        parentElem.appendChild(labHeader);
        parentElem.appendChild(labContent);

        // Add a click listener to the lab header to toggle the lab content
        labHeader.addEventListener("click", () => {
            labContent.classList.toggle("active");
            if (labContent.style.maxHeight) {
            labContent.style.maxHeight = null;
            } else {
            //labContent.style.maxHeight = labContent.scrollHeight + "px";
            labContent.style.maxHeight = "max-content"
            }
        });
    }
}
function displayGradeError(errorMessage) {
    document.getElementById('grades').innerHTML = `<p>${errorMessage}</p>`;
  }

function displayConvertTime(utcString) {

    //Add timestamp to the page
    dtstamp = document.getElementById('grading-timestamp')
    dtstamp.innerHTML = ''

    now = new Date();
    offset = (now.getTimezoneOffset());

    utcObj = new Date(utcString);
    newTime = new Date(utcObj - (offset * 60 * 1000));
    dtstamp.innerHTML = "Last graded at: " + newTime.toLocaleDateString() + ' ' + newTime.toLocaleTimeString() + '  (Beta version - Still under development)' 
}

document.getElementById("labBtn").addEventListener("click", function () {
    const selected_lab_options = document.querySelector('input[name="lab"]:checked').value;
    document.getElementById('loader').style.display = 'block'
    $.get("/lab?lab_value=" + selected_lab_options, (res) => {
        //console.log(res)
        if (res.response) {
            output = ''
            res.response.forEach(element => {
                output = output + element
            });
            document.getElementById('apiResponse').textContent = output
            document.getElementById('loader').style.display = 'none'

        }
    }).fail((err) => {
        //console.log(err)
        document.getElementById('apiResponse').textContent = "Some thing went wrong"
        document.getElementById('loader').style.display = 'none'
    })


});
function displayToolTip() {
    document.getElementById('tooltiptext').style.visibility = "visible";
}
function hideToolTip() {
    document.getElementById('tooltiptext').style.visibility = "hidden";
}
document.getElementById('CvpStatus').style.color = "grey";
document.getElementById('CvpStatus').style.pointerEvents = "none";



// For Popup

// Get the modal
var modal = document.getElementById("myModal");

// Get the button that opens the modal
var btn = document.getElementById("myBtn");

// Get the <span> element that closes the modal
var span = document.getElementsByClassName("close")[0];

// When the user clicks the button, open the modal 
btn.onclick = function () {
    modal.style.display = "block";
}

// When the user clicks on <span> (x), close the modal
span.onclick = function () {
    modal.style.display = "none";
}

// When the user clicks anywhere outside of the modal, close it
window.onclick = function (event) {
    if (event.target == modal) {
        modal.style.display = "none";
    }
}    