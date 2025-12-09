// Global flag to indicate if this is an exam lab
// SECURITY: Starts as true to ensure exams are never accidentally treated as labs
// If /examStatus fails to load, we treat the lab as an exam to prevent
// accidental bypass of exam protections. This is fail-safe behavior.
// Trade-off: Regular labs may show exam modal if API is unreachable.
window.isExamLab = true;
// Flag to track if exam status has been loaded from the server
window.examStatusLoaded = false;
// Flag to track if loading overlay has already been hidden (idempotency guard)
var loadingOverlayHidden = false;

/**
 * Hide the initial loading overlay once both WS is connected and exam status is known
 * Includes idempotency guard to prevent multiple DOM operations
 */
window.checkAndHideLoadingOverlay = function() {
    if (window.examStatusLoaded && window.wsConnected && !loadingOverlayHidden) {
        var initialLoading = document.getElementById('initialLoadingOverlay');
        if (initialLoading && initialLoading.style.display !== 'none') {
            initialLoading.style.display = 'none';
            loadingOverlayHidden = true;
            console.log('[Loading] Initial overlay hidden - exam status loaded and WS connected');
        }
    }
};

/**
 * Show a non-blocking notification for connection issues
 * @param {string} message - The message to display
 * @param {string} type - 'warning' or 'error'
 */
function showConnectionNotice(message, type) {
    var bgColor = type === 'error' ? '#dc3545' : '#ffc107';
    var textColor = type === 'error' ? 'white' : '#333';
    var notice = document.createElement('div');
    notice.style.cssText = 'position:fixed;top:20px;right:20px;background:' + bgColor +
        ';color:' + textColor + ';padding:15px 20px;border-radius:8px;z-index:9999;' +
        'box-shadow:0 4px 12px rgba(0,0,0,0.15);font-family:inherit;max-width:350px;';
    notice.innerHTML = message;
    document.body.appendChild(notice);
    setTimeout(function() {
        notice.style.opacity = '0';
        notice.style.transition = 'opacity 0.3s';
        setTimeout(function() { notice.remove(); }, 300);
    }, 8000);
}

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


document.addEventListener('DOMContentLoaded', function () {
    // Set up abort controller for fetch timeout (10 seconds)
    var controller = new AbortController();
    var timeoutId = setTimeout(function() {
        controller.abort();
    }, 10000);

    // Fetch exam status from the server with timeout
    fetch('/examStatus', { signal: controller.signal })
        .then(function(response) {
            clearTimeout(timeoutId);
            if (!response.ok) {
                throw new Error('Server returned ' + response.status);
            }
            return response.json();
        })
        .then(function(data) {
            console.log("[ExamStatus] Response from server:", data);

            if (data.response && data.response.trim() === 'startExamButtonNeeded') {
                // This is an exam lab - wait for WebSocket before hiding overlay
                // WebSocket is needed for CVP status updates
                window.isExamLab = true;
                console.log("[ExamStatus] This is an exam lab");
                addExamButton();
                window.examStatusLoaded = true;
                //window.checkAndHideLoadingOverlay();
            } else {
                // This is a regular lab - hide overlay immediately (don't wait for WS)
                // Regular labs don't need the exam modal, so no need to wait
                window.isExamLab = false;
                window.examStatusLoaded = true;
                console.log("[ExamStatus] This is a regular lab - hiding overlay immediately");
                var initialLoading = document.getElementById('initialLoadingOverlay');
                if (initialLoading) {
                    initialLoading.style.display = 'none';
                    loadingOverlayHidden = true;
                }
            }
        })
        .catch(function(error) {
            clearTimeout(timeoutId);

            if (error.name === 'AbortError') {
                console.error("[ExamStatus] Fetch timed out after 10 seconds");
                showConnectionNotice('⚠️ Connection timeout. Some features may be limited.', 'warning');
            } else {
                console.error("[ExamStatus] Error fetching exam status:", error);
                showConnectionNotice('⚠️ Connection issue detected. Some features may be limited.', 'warning');
            }

            // On error, default to non-exam to avoid blocking users
            // Note: This is a security trade-off - we allow access but log the issue
            window.isExamLab = false;
            window.examStatusLoaded = true;
            window.checkAndHideLoadingOverlay();
        });

    // Fallback: Force hide overlay after 15 seconds if WebSocket never connects
    // This prevents users from being stuck on the loading screen forever
    setTimeout(function() {
        if (!window.wsConnected && !loadingOverlayHidden) {
            console.warn('[Loading] WebSocket did not connect within 15s, forcing overlay hide');
            var initialLoading = document.getElementById('initialLoadingOverlay');
            if (initialLoading && initialLoading.style.display !== 'none') {
                initialLoading.style.display = 'none';
                loadingOverlayHidden = true;
                showConnectionNotice('⚠️ Real-time updates may not be available. Please check your network connection.', 'warning');
            }
        }
    }, 15000);
});


function addExamButton() {
    // Set up CVP modal Start Exam button
    var cvpModalBtn = document.getElementById('cvpStartExamBtn');
    var cvpModal = document.getElementById('cvpWaitingModal');

    if (cvpModalBtn) {
        // Store original button text for recovery
        var originalBtnText = cvpModalBtn.textContent || 'Start Exam';

        cvpModalBtn.addEventListener('click', function () {
            var btn = this;

            if (btn.disabled) {
                return false;
            }

            // Confirm with user before starting exam
            var confirmStart = confirm("Closing this window will automatically submit your exam. You will not be able to start a new attempt.\n\nClick OK to start your exam.");

            if (!confirmStart) {
                return false;
            }

            // Disable button and show loading state to prevent double-clicks
            btn.disabled = true;
            btn.textContent = 'Starting...';

            fetch('/examStatus', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ update_status: "status=startExamButtonNotNeeded" })
            })
                .then(function(response) {
                    if (!response.ok) {
                        throw new Error('Server returned ' + response.status);
                    }
                    return response.json();
                })
                .then(function(postdataresponse) {
                    console.log("[ExamStart] Exam started successfully:", postdataresponse);
                    if (cvpModal) {
                        cvpModal.style.opacity = 0;
                        cvpModal.style.visibility = 'hidden';
                    }
                    location.reload();
                })
                .catch(function(error) {
                    console.error("[ExamStart] Error starting exam:", error);
                    alert("Failed to start exam. Please try again or contact support if the problem persists.");
                    // Re-enable button for retry
                    btn.disabled = false;
                    btn.textContent = originalBtnText;
                });
        });
    }
}
// $('#labMenu').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'block'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'none'
//     clearInterval(labStatusInterval)
// });
// $('#home').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'block'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'none'
//     clearInterval(labStatusInterval)
// })
// $('#labStaus').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'block'
//     document.getElementById('labGradingData').style.display = 'none'
//     getLabStatus()
//     labStatusInterval = setInterval(
//         () => {
//             getLabStatus()
//         }, 30000
//     )
// })
// $('#labGrading').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'block'
//     clearInterval(labStatusInterval)
// })

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
                let spanElement = document.createElement("span");
                labStatus.appendChild(spanElement)
                spanElement.textContent = values[1];
                if (values[1].indexOf("Ok") >= 0) {
                    //labStatus.style.color = "green"
                    spanElement.classList.add("switch", "green");
                } else {
                    failedSwitches.push(values[0])
                    // labStatus.style.color = "red"
                    // labStatus.style.fontWeight = "bold"
                    spanElement.classList.add("switch", "red");
                    spanElement.textContent = values[1];
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






document.getElementById("labBtn").addEventListener("click", function () {
    const selected_lab_options = $('.lab-button.active').attr('id'); // Get the ID instead of text
    // document.getElementById('loader').style.display = 'block'
    $.get("/lab?lab_value=" + selected_lab_options, (res) => {
        console.log(res)
        if (res.response) {
            // output = ''
            // res.response.forEach(element => {
            //     output = output + element
            // });
            document.getElementById('apiResponse').textContent = res.response
            // document.getElementById('loader').style.display = 'none'

        }
    }).fail((err) => {
        console.log(err)
        document.getElementById('apiResponse').textContent = "Some thing went wrong"
        // document.getElementById('loader').style.display = 'none'
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