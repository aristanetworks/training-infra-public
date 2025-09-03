const labStaustext = document.getElementById("labStatusByApi");
if (labStaustext) { // Check if element exists
    labStaustext.innerHTML = "<td>Please wait, Lab Status is being loaded...</td>";
}
let failedSwitches = [];
// Corrected: checkIsAllSwitchesOK was not declared before assignment
let isAllSwitchesOk = true;
const checkIsAllSwitchesOKStorage = localStorage.getItem('isAllSwitchesOk');

if (checkIsAllSwitchesOKStorage === 'yes' || checkIsAllSwitchesOKStorage === undefined || checkIsAllSwitchesOKStorage === null) {
    isAllSwitchesOk = true;
} else {
    isAllSwitchesOk = false;
}
let resetRequestSubmittedTime = localStorage.getItem('resetRequestSubmittedTime'); // Corrected: resetRequestSubmittedTime was not declared
const resetOkMSGEl = document.getElementById('resetOkMSG');
if (resetRequestSubmittedTime && resetOkMSGEl) { // Check if element exists
    resetOkMSGEl.innerHTML = "Please wait, reset request has been submitted at " + resetRequestSubmittedTime;
}

document.addEventListener('DOMContentLoaded', function () {
    const overlay = document.getElementById('overlay');
    if (!overlay) {
        console.error("#overlay element not found. Cannot initialize exam UI.");
        return;
    }

    // or just let the CVP wait message (handled by atd-ws.js) act as a loading indicator.
    // For now, we ensure the overlay is hidden until we know if the button is needed.
    overlay.style.display = 'none';
    overlay.style.visibility = 'hidden';
    overlay.style.opacity = 0;

    fetch('/examStatus') // Fetch exam status from the Tornado server
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            console.log("Response from /examStatus GET:", data);
            if (data.response && data.response.trim() === 'startExamButtonNeeded') {
                console.log("Exam button is needed. Setting up overlay.");
                // Make the main overlay (which contains the full structure from index.html) visible.
                overlay.style.display = 'flex'; // Or 'flex' if that's your layout
                overlay.style.visibility = 'visible';
                overlay.style.opacity = 1;
                addExamButtonAndListener(); // Call the modified function
            } else {
                console.log("Exam button not needed. Overlay will remain hidden.");
                overlay.style.display = 'none';
                overlay.style.visibility = 'hidden';
                overlay.style.opacity = 0;
            }
        })
        .catch(error => {
            console.error("Error fetching exam status:", error);
            if (overlay) { // Ensure overlay is hidden on error
                overlay.style.display = 'none';
                overlay.style.visibility = 'hidden';
                overlay.style.opacity = 0;
            }
        });
});

/**
 * Finds the existing "Start Exam" button in the HTML (from index.html)
 * and attaches its click event listener.
 * This function no longer modifies overlay.innerHTML.
 */
function addExamButtonAndListener() {
    const overlay = document.getElementById('overlay');
    const overlayButton = document.getElementById('overlayButton'); // Get button from index.html

    if (!overlay) {
        console.error("Main #overlay element not found. Cannot attach listener.");
        return;
    }
    if (!overlayButton) {
        console.error("#overlayButton not found within #overlay. Ensure index.html structure is correct and loaded.");
        // If #overlayButton is missing, atd-ws.js will also have trouble.
        // This indicates index.html is still not providing the expected structure.
        return;
    }

    console.log("#overlayButton found. Attaching click listener.");

    overlayButton.addEventListener('click', function () {
        // This check is important: atd-ws.js might disable this button if CVP is not ready
        if (overlayButton.disabled) {
            console.log("Start Exam button is currently disabled (likely CVP not ready). Click action ignored.");
            // You could alert the user or briefly show a message here.
            alert("Please wait for CVP to be ready before starting the exam. The status is shown in the overlay.");
            return;
        }

        console.log("Start Exam button clicked. Sending POST to /examStatus.");
        fetch('/examStatus', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // This body was in your original script. Ensure it's what your server expects.
            body: JSON.stringify({ update_status: "status=startExamButtonNotNeeded" })
        })
        .then(response => {
            if (!response.ok) {
                // Try to get more error info if possible
                return response.json().then(err => {
                    throw new Error(`Server error: ${response.status} - ${err.error || 'Unknown error'}`);
                }).catch(() => { // If parsing error JSON fails
                    throw new Error(`Server error: ${response.status} - ${response.statusText}`);
                });
            }
            return response.json();
        })
        .then(postdataresponse => {
            console.log("Response from server after /examStatus POST:", postdataresponse);
            // Hide the entire overlay and reload
            overlay.style.opacity = 0;
            overlay.style.visibility = 'hidden';
            overlay.style.display = 'none'; // Ensure it's fully hidden
            location.reload();
        })
        .catch(error => {
            console.error("Error POSTing to /examStatus or processing response:", error);
            alert(`Failed to start the exam: ${error.message}. Please try again.`);
        });
    });
}


$('#resetLabs').click((event) => {
    if (confirm('Please click ok to reset below switches \n-' + failedSwitches.join('\n-'))) {
        const resetLabsEl = document.getElementById('resetLabs');
        const resetOkMSGInnerEl = document.getElementById('resetOkMSG'); // Renamed to avoid conflict
        if(resetLabsEl) resetLabsEl.innerHTML = '';
        const timestamp = new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString();
        if(resetOkMSGInnerEl) resetOkMSGInnerEl.innerHTML = "Please wait, reset request has been submitted at " + timestamp;

        isAllSwitchesOk = false; // This was declared with let earlier, ensure scope is correct or re-declare if needed
        localStorage.setItem('isAllSwitchesOk', 'no');
        localStorage.setItem('resetRequestSubmittedTime', timestamp);

        $.get('/resetLab?lab_names=' + failedSwitches.join(','), (res) => {
            // Handle response if needed
            console.log("Reset lab response:", res);
        });
    }
});

function getLabStatus() {
    let switchesTable = document.getElementById("labStatusByApi");
    if (!switchesTable) return; // Exit if table not found

    $.get('/labStaus', (res) => {
        switchesTable.innerHTML = ""; // Clear previous status
        failedSwitches = [];
        if (res && res.response && Array.isArray(res.response)) {
            res.response.forEach(
                (item) => {
                    let row = document.createElement("tr");
                    let labNameCell = document.createElement("td"); // Renamed for clarity
                    let values = item.split(',');
                    labNameCell.innerHTML = values[0];

                    let labStatusCell = document.createElement("td"); // Renamed for clarity
                    let spanElement = document.createElement("span");
                    labStatusCell.appendChild(spanElement);
                    spanElement.textContent = values[1] ? values[1].trim() : 'N/A'; // Handle missing status

                    if (values[1] && values[1].trim().toLowerCase().includes("ok")) {
                        spanElement.classList.add("switch", "green");
                    } else {
                        failedSwitches.push(values[0]);
                        spanElement.classList.add("switch", "red");
                    }
                    row.appendChild(labNameCell);
                    row.appendChild(labStatusCell);
                    switchesTable.appendChild(row);
                }
            );
        } else {
            switchesTable.innerHTML = "<tr><td colspan='2'>Could not parse lab status response or no data.</td></tr>";
            console.error("Invalid response structure from /labStaus:", res);
        }

        const resetOkMSGEl = document.getElementById('resetOkMSG');
        const resetLabsEl = document.getElementById('resetLabs');

        if (failedSwitches.length === 0) {
            isAllSwitchesOk = true;
            localStorage.setItem('isAllSwitchesOk', 'yes');
            if (resetOkMSGEl) resetOkMSGEl.innerHTML = '';
            localStorage.removeItem('resetRequestSubmittedTime');
        }

        if (isAllSwitchesOk && failedSwitches.length > 0) {
            if (resetLabsEl) resetLabsEl.innerHTML = 'Please <span style="color: blue; text-decoration: underline; cursor: pointer;">click here</span> to reset failed switches';
        } else {
            if (resetLabsEl) resetLabsEl.innerHTML = '';
        }
        const lastUpdatedEl = document.getElementById('lastUpdated');
        if (lastUpdatedEl) lastUpdatedEl.innerHTML = "Last updated : " + new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString();
    }).fail((jqXHR, textStatus, errorThrown) => {
        console.error("Error fetching /labStaus:", textStatus, errorThrown);
        if(switchesTable) switchesTable.innerHTML = "<tr><td colspan='2'>Error loading lab status.</td></tr>";
    });
}

// Lab button click (kept from your original code)
const labBtnEl = document.getElementById("labBtn");
if (labBtnEl) { // Check if element exists
    labBtnEl.addEventListener("click", function () {
        const selected_lab_options_el = $('.lab-button.active');
        if (!selected_lab_options_el.length) {
            alert("Please select a lab option first.");
            return;
        }
        const selected_lab_options = selected_lab_options_el.attr('id');
        const apiResponseEl = document.getElementById('apiResponse');
        if (!apiResponseEl) return;

        // document.getElementById('loader').style.display = 'block' // Assuming you have a loader element
        $.get("/lab?lab_value=" + selected_lab_options, (res) => {
            console.log("Response from /lab:", res);
            if (res.response) {
                apiResponseEl.textContent = res.response;
            } else {
                apiResponseEl.textContent = "Received an empty response from the lab action.";
            }
            // document.getElementById('loader').style.display = 'none'
        }).fail((err) => {
            console.error("Error calling /lab:", err);
            apiResponseEl.textContent = "Something went wrong while processing the lab action.";
            // document.getElementById('loader').style.display = 'none'
        });
    });
}

// Tooltip functions (kept from your original code)
function displayToolTip(element) { // Added element parameter for context if needed
    const tooltipText = document.getElementById('tooltiptext');
    if (tooltipText) tooltipText.style.visibility = "visible";
}
function hideToolTip(element) { // Added element parameter for context if needed
    const tooltipText = document.getElementById('tooltiptext');
    if (tooltipText) tooltipText.style.visibility = "hidden";
}

// CVP Status styling (ensure #CvpStatus exists or handle potential null)
const cvpStatusLink = document.getElementById('CvpStatus'); // Assuming this ID exists for the CVP link
if (cvpStatusLink) {
    console.warn("Initial styling for #CvpStatus is present. This might be handled by atd-ws.js based on CVP readiness now.");
} else {
    // console.warn("#CvpStatus element not found for initial styling.");
}


// For Popup (kept from your original code)
var modal = document.getElementById("myModal");
var btn = document.getElementById("myBtn"); // This is the "Passwords" button
var span = document.getElementsByClassName("close")[0]; 

if (btn && modal && span) { // Ensure all elements for modal exist
    btn.onclick = function () {
        modal.style.display = "block";
    }
    span.onclick = function () {
        modal.style.display = "none";
    }
    window.onclick = function (event) {
        if (event.target == modal) {
            modal.style.display = "none";
        }
    }
} else {
    // console.warn("Modal elements (myModal, myBtn, or close button) not all found. Popup functionality might be affected.");
}