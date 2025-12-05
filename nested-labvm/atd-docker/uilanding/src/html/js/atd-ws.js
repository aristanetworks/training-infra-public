var atdURL = window.location.origin;
if (atdURL.includes('https')) {
    atdURL = atdURL.replace("https:", "wss:");
}
else {
    atdURL = atdURL.replace("http:", "ws:");
}
atdURL += "/td-ws";
var ws = new WebSocket(atdURL);
var event_timer_ids = {};
var topo_notify = false;
var notifications_sent = {
    "1hr": false,
    "30min": false,
    "10min": false
};
createWS(atdURL);


function createWS(SOCK_URL) {
    // Create a websocket connection
    ws = new WebSocket(SOCK_URL);
    ws.onopen = function () {
        // Update connectivity monitor
        if (window.ConnectivityMonitor) {
            window.ConnectivityMonitor.updateWSStatus(true);
        }

        // Web Socket is connected, send data using send()
        ws.send(JSON.stringify({
            type: "hello",
            data: {
                action: "status"
            }
        }));
    };

    ws.onclose = function (evt) {
        // Update connectivity monitor
        if (window.ConnectivityMonitor) {
            window.ConnectivityMonitor.updateWSStatus(false);
        }

        if (!evt.wasClean) {
            setTimeout(function () {
                createWS(SOCK_URL);
            }, 500)
        }
    }

    ws.onmessage = function (evt) {
        var re_data = evt.data;
        var received_msg = JSON.parse(re_data);
        console.log(received_msg)
        reg_data = received_msg['data'];

        // Hide initial loading overlay once we receive first message
        var initialLoading = document.getElementById('initialLoadingOverlay');
        // Hide the original overlay and show CVP waiting modal
        if (initialLoading) {
            initialLoading.style.display = 'none';
        }
        // Handle Start Exam button visibility and state based on CVP status
        // Only show modal if this is an exam lab
        var isExamLab = window.isExamLab || false;
        var overlay = document.getElementById('overlay');
        var cvpModal = document.getElementById('cvpWaitingModal');
        var cvpModalBtn = document.getElementById('cvpStartExamBtn');
        var cvpModalTitle = document.getElementById('cvpModalTitle');
        var cvpModalMessage = document.getElementById('cvpModalMessage');
        var cvpNoticeText = document.getElementById('cvpNoticeText');
        var cvpSuggestion = document.getElementById('cvpSuggestion');
        var cvpLoadingIcon = document.getElementById('cvpLoadingIcon');
        var cvpReadyIcon = document.getElementById('cvpReadyIcon');

        if (reg_data['cvp'] && reg_data['cvp']['status'] && reg_data['cvp']['status'] != 'UP') {
            // Only show modal for exam labs
            if (isExamLab) {
                // Show CVP waiting modal with waiting state
                if (cvpModal) {
                    cvpModal.style.display = 'flex';
                }

                // Show loading icon, hide ready icon
                if (cvpLoadingIcon) cvpLoadingIcon.style.display = 'block';
                if (cvpReadyIcon) cvpReadyIcon.style.display = 'none';

                // Set waiting state messages
                if (cvpModalTitle) cvpModalTitle.textContent = 'CVP is Starting';
                if (cvpModalMessage) {
                    cvpModalMessage.innerHTML = 'Please wait for CVP to start, this can take <strong>15 minutes</strong>.';
                }
                if (cvpNoticeText) {
                    cvpNoticeText.innerHTML = 'This time is <strong>NOT</strong> part of your allocated exam.';
                }
                if (cvpSuggestion) {
                    cvpSuggestion.innerHTML = 'Please leave this tab open and grab a drink or use the restroom. The <strong>Start Exam</strong> button will be enabled once CVP is up.';
                }

                // Disable the Start Exam button in modal
                if (cvpModalBtn) {
                    cvpModalBtn.disabled = true;
                }
            }

            btn = document.getElementById('labBtn')
            if (btn) {
                btn.disabled = true
                cvp = document.getElementById('cvpStatus')
                if (cvp) {
                    cvp.textContent = "CVP is currently starting, Lab menu will be available once CVP is up"
                    document.getElementById('cvpLoading').style.display = "block"
                    document.getElementById('cvpLoaded').style.display = "none"
                }
            }
        } else {
            // CVP is UP
            // Only update modal for exam labs
            if (isExamLab) {
                // Update modal to ready state
                if (cvpModal) {
                    cvpModal.style.display = 'flex';
                }

                // Hide loading icon, show ready icon
                if (cvpLoadingIcon) cvpLoadingIcon.style.display = 'none';
                if (cvpReadyIcon) cvpReadyIcon.style.display = 'block';

                // Set ready state messages
                if (cvpModalTitle) cvpModalTitle.textContent = 'CVP is Ready!';
                if (cvpModalMessage) {
                    cvpModalMessage.innerHTML = 'CVP has successfully started and is ready for your exam.';
                }
                if (cvpNoticeText) {
                    cvpNoticeText.innerHTML = 'You can now start your exam. <strong>Good luck!</strong>';
                }
                if (cvpSuggestion) {
                    cvpSuggestion.innerHTML = 'Click the <strong>Start Exam</strong> button below to begin.';
                }

                // Enable the Start Exam button in modal
                if (cvpModalBtn) {
                    cvpModalBtn.disabled = false;
                }

                // Hide the original overlay
                if (overlay) {
                    overlay.style.display = 'none';
                }
            }

            btn = document.getElementById('labBtn')
            if (btn) {
                btn.disabled = false
                cvp = document.getElementById('cvpStatus')
                if (cvp) {
                    cvp.textContent = ""
                    document.getElementById('cvpLoading').style.display = "none"
                    document.getElementById('cvpLoaded').style.display = "block"
                }
            }
        }
        if (received_msg['type'] == 'ping') {
            // Update connectivity monitor on successful ping
            if (window.ConnectivityMonitor) {
                window.ConnectivityMonitor.updateWSStatus(true);
            }

            ws.send(JSON.stringify({
                type: "pong",
                data: {
                    message: 'pong'
                }
            }));
        }
        else if (received_msg['type'] == 'status') {
            var reg_data = received_msg['data'];
            if ('uptime' in reg_data) {
                uptime_data = reg_data['uptime'];
                if (reg_data['endexamtime'] !== 0) {
                    examInstanceCountdown('countdown_timer', reg_data['endexamtime']);
                }
                else {
                    instanceCountdown('countdown_timer', uptime_data['boottime'], uptime_data['runtime'])
                }
            }
            if ('cvp' in reg_data) {
                _cvp_info = "<h3>CVP " + reg_data['cvp']['version'] + " is currently " + reg_data['cvp']['status'] + "</h3>";
                if ('tasks' in reg_data) {
                    if (reg_data['tasks']) {
                        if (reg_data['tasks']['status'] == 'Active') {
                            // Loop through all the tasks
                            if (reg_data['tasks']['tasks']) {
                                _cvp_info += "Currently ";
                                for (_status in reg_data['tasks']['tasks']) {
                                    _cvp_info += reg_data['tasks']['tasks'][_status] + " " + _status + " tasks.";
                                }
                            }
                        }
                        else {
                            _cvp_info += "No pending tasks in CVP.";
                        }
                    }
                }
                document.getElementById("cvp_info").innerHTML = _cvp_info
            }
            ws.send(JSON.stringify({
                type: "update",
                data: {
                    message: 'ACK'
                }
            }));
        }
    }
}

function instanceCountdown(element, boot_time, runtime) {
    var el = document.getElementById(element);
    var countdown_string = '';
    var count_style = 'white';
    if (event_timer_ids.hasOwnProperty(element)) {
        clearInterval(event_timer_ids[element]);
        delete event_timer_ids[element];
    }
    var interval = setInterval(function () {
        const countdown_diff = (boot_time + (runtime * 60 * 60)) - Math.floor(new Date().getTime() / 1000);
        if (countdown_diff > 0) {
            const countdown_parts = {
                hours: Math.floor((countdown_diff / (60 * 60)) % 24),
                minutes: Math.floor((countdown_diff / 60) % 60),
                seconds: Math.floor((countdown_diff) % 60)
            }
            if (countdown_diff < (30 * 60)) {
                count_style = 'red';
                // check to see if user has been notified
                if (!topo_notify) {
                    alert("Your topology will shutdown in " + countdown_parts['minutes'] + " minutes.");
                    topo_notify = true;
                }
            }
            countdown_string = countdown_parts['hours'].toString().padStart(2, 0) + ':' + countdown_parts['minutes'].toString().padStart(2, 0) + ':' + countdown_parts['seconds'].toString().padStart(2, 0);
        }
        else {
            countdown_string = '00:00:00';
        }
        el.innerHTML = countdown_string;
        el.style.color = count_style;
    }, 1000);
    event_timer_ids[element] = interval;
}


function examInstanceCountdown(element, exam_end_time) {
    var el = document.getElementById(element);
    var countdown_string = '';
    var count_style = 'white';
    // Track which notifications have been sent
    if (event_timer_ids.hasOwnProperty(element)) {
        clearInterval(event_timer_ids[element]);
        delete event_timer_ids[element];
    }
    var interval = setInterval(function () {
        const countdown_diff = (exam_end_time) - Math.floor(new Date().getTime() / 1000);
        if (countdown_diff > 0) {
            const countdown_parts = {
                hours: Math.floor((countdown_diff / (60 * 60)) % 24),
                minutes: Math.floor((countdown_diff / 60) % 60),
                seconds: Math.floor((countdown_diff) % 60)
            }
            if (countdown_diff < (30 * 60)) {
                count_style = 'red';
                // check to see if user has been notified
                // Notify at 1 hour, 30 minutes, and 10 minutes left
                if (countdown_diff <= (60 * 60) && !notifications_sent["1hr"]) {
                    alert("Your topology will shutdown in 1 hour.");
                    notifications_sent["1hr"] = true;
                }
                if (countdown_diff <= (30 * 60) && !notifications_sent["30min"]) {
                    alert("Your topology will shutdown in 30 minutes.");
                    notifications_sent["30min"] = true;
                    count_style = 'orange';  // Change text color to orange at 30 min
                }
                if (countdown_diff <= (10 * 60) && !notifications_sent["10min"]) {
                    alert("Your topology will shutdown in 10 minutes.");
                    notifications_sent["10min"] = true;
                    count_style = 'red';  // Change text color to red at 10 min
                }
            }
            countdown_string = countdown_parts['hours'].toString().padStart(2, 0) + ':' + countdown_parts['minutes'].toString().padStart(2, 0) + ':' + countdown_parts['seconds'].toString().padStart(2, 0);
        }
        else {
            countdown_string = '00:00:00';
            clearInterval(interval); // Stop countdown when time runs out
            console.log("Exam submitted:", data);
            alert("Exam has been automatically submitted.");
            // Fetch examSubmit when timer reaches 0 using GET request
            // fetch('/examSubmit', {
            //     method: 'GET',
            //     headers: {
            //         'Content-Type': 'application/json'
            //     }
            // })
            // .then(response => response.json())
            // .then(data => {
            //     console.log("Exam submitted:", data);
            //     alert("Exam has been automatically submitted.");
            // })
            // .catch(error => console.error("Error submitting exam:", error));
        }
        el.innerHTML = countdown_string;
        el.style.color = count_style;
    }, 1000);
    event_timer_ids[element] = interval;
}
