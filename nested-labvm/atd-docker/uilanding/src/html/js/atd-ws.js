// js/atd-ws.js
var atdURL = window.location.origin;
if ( atdURL.includes('https') ) {
    atdURL = atdURL.replace("https:","wss:");
}
else {
    atdURL = atdURL.replace("http:","ws:");
}

atdURL += "/td-ws";
var ws = new WebSocket(atdURL); // ws is declared with var
var event_timer_ids = {};
var topo_notify = false;
var notifications_sent = {
    "1hr": false,
    "30min": false,
    "10min": false
};
createWS(atdURL);


function createWS(SOCK_URL) {
    ws = new WebSocket(SOCK_URL); // Re-assigns the global/function-scoped ws
    ws.onopen = function() {
        ws.send(JSON.stringify({
            type:"hello",
            data: {
                action: "status"
            }
        }));
    };

    ws.onclose = function(evt) {
        if ( !evt.wasClean ) {
            setTimeout(function() {
                createWS(SOCK_URL);
            },500)
        }
    }

    ws.onmessage = function (evt) {
        var re_data = evt.data;
        var received_msg = JSON.parse(re_data);
        console.log("Received WebSocket message:", received_msg); // General log for any message

        var msg_payload = received_msg['data']; // General data payload from the message

        // Get references to common UI elements for CVP status
        let labBtnEl = document.getElementById('labBtn');
        let cvpStatusDisplayEl = document.getElementById('cvpStatus');
        let cvpLoadingIndicatorEl = document.getElementById('cvpLoading');
        let cvpLoadedIndicatorEl = document.getElementById('cvpLoaded');

        // Get references to NEW overlay elements
        let overlayBtnEl = document.getElementById('overlayButton');
        let cvpWaitMsgContainerEl = document.getElementById('cvpWaitMessageOverlayContainer');
        let cvpWaitMsgEl = document.getElementById('cvpWaitMessageOverlay');
        let startExamContentContainerEl = document.getElementById('startExamContentContainer');

        // This logic handles UI elements based on CVP status from any message that contains it.
        if (msg_payload && msg_payload['cvp'] && typeof msg_payload['cvp']['status'] !== 'undefined') {
            if (msg_payload['cvp']['status'] !== 'UP') {
                // CVP is NOT UP
                console.log("[CVP_STATUS_CHECK] CVP is NOT UP. Updating UI elements.");

                if (labBtnEl) labBtnEl.disabled = true;
                if (cvpStatusDisplayEl) cvpStatusDisplayEl.textContent = "CVP is currently starting, Lab menu will be available once CVP is up";
                if (cvpLoadingIndicatorEl) cvpLoadingIndicatorEl.style.display = "block";
                if (cvpLoadedIndicatorEl) cvpLoadedIndicatorEl.style.display = "none";

                // --- Start Enhanced Debugging for Overlay CVP NOT UP ---
                console.log("[CVP NOT UP - OVERLAY] Processing overlay elements.");
                console.log("[CVP NOT UP - OVERLAY] overlayBtnEl:", overlayBtnEl);
                console.log("[CVP NOT UP - OVERLAY] cvpWaitMsgContainerEl:", cvpWaitMsgContainerEl);
                console.log("[CVP NOT UP - OVERLAY] cvpWaitMsgEl:", cvpWaitMsgEl);
                console.log("[CVP NOT UP - OVERLAY] startExamContentContainerEl:", startExamContentContainerEl);

                if (overlayBtnEl) {
                    overlayBtnEl.disabled = true;
                    console.log("[CVP NOT UP - OVERLAY] overlayBtnEl disabled.");
                }

                if (cvpWaitMsgEl) {
                    cvpWaitMsgEl.textContent = "CVP is currently starting. Please wait to start the exam.";
                    // Explicitly make the paragraph visible too
                    cvpWaitMsgEl.style.display = "block";
                    cvpWaitMsgEl.style.visibility = "visible";
                    cvpWaitMsgEl.style.opacity = "1";
                    console.log("[CVP NOT UP - OVERLAY] cvpWaitMsgEl text content set and styled for visibility.");
                } else {
                    console.error("[CVP NOT UP - OVERLAY] cvpWaitMsgEl (paragraph for wait message) NOT FOUND!");
                }

                if (cvpWaitMsgContainerEl) {
                    cvpWaitMsgContainerEl.style.display = "block";
                    // Add more styles to ensure visibility for debugging
                    cvpWaitMsgContainerEl.style.visibility = "visible";
                    cvpWaitMsgContainerEl.style.opacity = "1";
                    cvpWaitMsgContainerEl.style.height = "auto"; // Ensure it takes height of content
                    cvpWaitMsgContainerEl.style.minHeight = "30px"; // Give it some minimum height to be noticeable
//                    cvpWaitMsgContainerEl.style.backgroundColor = "rgba(50, 50, 0, 0.5)"; // Temporary dark yellow background to see its area
                    console.log("[CVP NOT UP - OVERLAY] cvpWaitMsgContainerEl styled to be visible. Current inline display:", cvpWaitMsgContainerEl.style.display);
                    // Note: For computed style, it's best to check in browser dev tools as it reflects all CSS sources.
                    // console.log("[CVP NOT UP - OVERLAY] cvpWaitMsgContainerEl computed display (may differ if CSS overrides):", window.getComputedStyle(cvpWaitMsgContainerEl).display);
                } else {
                    console.error("[CVP NOT UP - OVERLAY] cvpWaitMsgContainerEl (container for wait message) NOT FOUND!");
                }

                if (startExamContentContainerEl) {
                    startExamContentContainerEl.style.display = "none";
                    console.log("[CVP NOT UP - OVERLAY] startExamContentContainerEl hidden.");
                }
                // --- End Enhanced Debugging for Overlay CVP NOT UP ---

            } else {
                // CVP IS UP
                console.log("[CVP_STATUS_CHECK] CVP IS UP. Updating UI elements.");

                if (labBtnEl) labBtnEl.disabled = false;
                if (cvpStatusDisplayEl) cvpStatusDisplayEl.textContent = "";
                if (cvpLoadingIndicatorEl) cvpLoadingIndicatorEl.style.display = "none";
                if (cvpLoadedIndicatorEl) cvpLoadedIndicatorEl.style.display = "block";

                // Handle overlay for CVP UP
                console.log("[CVP IS UP - OVERLAY] Processing overlay elements.");
                if (overlayBtnEl) {
                    overlayBtnEl.disabled = false;
                    console.log("[CVP IS UP - OVERLAY] overlayBtnEl enabled.");
                }
                if (cvpWaitMsgContainerEl) {
                    cvpWaitMsgContainerEl.style.display = "none"; // Hide message container
                    cvpWaitMsgContainerEl.style.backgroundColor = ""; // Clear temporary background
                    console.log("[CVP IS UP - OVERLAY] cvpWaitMsgContainerEl hidden.");
                }
                if (startExamContentContainerEl) {
                    startExamContentContainerEl.style.display = "block"; // Show start exam content
                    console.log("[CVP IS UP - OVERLAY] startExamContentContainerEl shown.");
                }
            }
        } else {
            console.warn("[CVP_STATUS_CHECK] CVP status information not found in message payload or payload is missing. UI related to CVP status may not update.", msg_payload);
        }

        if (received_msg['type'] == 'ping') {
            ws.send(JSON.stringify({
                type: "pong",
                data: {
                    message: 'pong'
                }
            }));
        }
        else if (received_msg['type'] == 'status') {
            // This block handles specific updates for 'status' type messages like uptime and detailed CVP info.
            // The CVP status check above already handled button/link enable/disable states.
            var status_specific_payload = received_msg['data']; // Use a different variable name for clarity

            if ('uptime' in status_specific_payload) {
                let uptime_data = status_specific_payload['uptime'];
                if (status_specific_payload['endexamtime'] !== 0){
                    examInstanceCountdown('countdown_timer', status_specific_payload['endexamtime']);
                } else {
                    instanceCountdown('countdown_timer', uptime_data['boottime'], uptime_data['runtime']);
                }
            }
            if ('cvp' in status_specific_payload) { // This is for the #cvp_info display on the homepage
                let cvp_info_display_el = document.getElementById("cvp_info");
                if (cvp_info_display_el) {
                    let _cvp_info = "<h3>CVP " + status_specific_payload['cvp']['version'] + " is currently " + status_specific_payload['cvp']['status'] + "</h3>";
                    if ('tasks' in status_specific_payload && status_specific_payload['tasks']) {
                        if (status_specific_payload['tasks']['status'] == 'Active') {
                            if (status_specific_payload['tasks']['tasks']) {
                                _cvp_info += "Currently ";
                                for (let _task_status in status_specific_payload['tasks']['tasks']){
                                    _cvp_info += status_specific_payload['tasks']['tasks'][_task_status] + " " + _task_status + " tasks.";
                                }
                            }
                        } else {
                            _cvp_info += "No pending tasks in CVP.";
                        }
                    }
                    cvp_info_display_el.innerHTML = _cvp_info;
                }
            }
            ws.send(JSON.stringify({
                type: "update",
                data: {
                    message: 'ACK'
                }
            }));
        }
    }; // Semicolon for end of function assignment
} // Semicolon for end of function definition

function instanceCountdown(element, boot_time, runtime) {
    var el = document.getElementById(element);
    var countdown_string = '';
    var count_style = 'white';
    if ( event_timer_ids.hasOwnProperty(element) ) {
        clearInterval(event_timer_ids[element]);
        delete event_timer_ids[element];
    }
    var interval = setInterval(function() {
        const countdown_diff = (boot_time + ( runtime * 60 * 60 )) - Math.floor( new Date().getTime() / 1000 );
        if ( countdown_diff > 0 ) {
            const countdown_parts = {
                hours: Math.floor((countdown_diff / (60 * 60)) % 24),
                minutes: Math.floor((countdown_diff / 60) % 60),
                seconds: Math.floor((countdown_diff) % 60)
            }
            if (countdown_diff < (30 * 60) ) {
                count_style = 'red';
                if ( !topo_notify ) {
                    alert("Your topology will shutdown in " + countdown_parts['minutes'] + " minutes.");
                    topo_notify = true;
                }
            }
            countdown_string = countdown_parts['hours'].toString().padStart(2,0) + ':' + countdown_parts['minutes'].toString().padStart(2,0) + ':' + countdown_parts['seconds'].toString().padStart(2,0);
        }
        else {
            countdown_string = '00:00:00';
            // Optionally stop interval if needed, though it just updates text to 00:00:00
            // clearInterval(interval);
        }
        if (el) { // Check if element exists before updating
            el.innerHTML = countdown_string;
            el.style.color = count_style;
        } else {
            clearInterval(interval); // Stop if element is gone
        }
    }, 1000);
    event_timer_ids[element] = interval;
}

function examInstanceCountdown(element, exam_end_time) {
    var el = document.getElementById(element);
    var countdown_string = '';
    var count_style = 'white';
    if ( event_timer_ids.hasOwnProperty(element) ) {
        clearInterval(event_timer_ids[element]);
        delete event_timer_ids[element];
    }
    var interval = setInterval(function() {
        const countdown_diff = (exam_end_time ) - Math.floor( new Date().getTime() / 1000 );
        if ( countdown_diff > 0 ) {
            const countdown_parts = {
                hours: Math.floor((countdown_diff / (60 * 60)) % 24),
                minutes: Math.floor((countdown_diff / 60) % 60),
                seconds: Math.floor((countdown_diff) % 60)
            }
            // Notifications logic
            if (countdown_diff <= (60 * 60) && !notifications_sent["1hr"]) {
              alert("Your topology will shutdown in 1 hour.");
              notifications_sent["1hr"] = true;
            }
            if (countdown_diff <= (30 * 60) && !notifications_sent["30min"]) {
                alert("Your topology will shutdown in 30 minutes.");
                notifications_sent["30min"] = true;
                count_style = 'orange';
            }
            if (countdown_diff <= (10 * 60) && !notifications_sent["10min"]) {
                alert("Your topology will shutdown in 10 minutes.");
                notifications_sent["10min"] = true;
                count_style = 'red';
            }
            // If already under 30 or 10 min and notifications were sent, ensure color stays
            else if (countdown_diff <= (10*60)) { count_style = 'red';}
            else if (countdown_diff <= (30*60)) { count_style = 'orange';}


            countdown_string = countdown_parts['hours'].toString().padStart(2,0) + ':' + countdown_parts['minutes'].toString().padStart(2,0) + ':' + countdown_parts['seconds'].toString().padStart(2,0);
        }
        else {
            countdown_string = '00:00:00';
            clearInterval(interval);
            alert("Exam has been automatically submitted.");
            // Fetch examSubmit logic (commented out in original)
            // fetch('/examSubmit', { /* ... */ }) // .then ... .catch ...
        }
        if (el) { // Check if element exists before updating
            el.innerHTML = countdown_string;
            el.style.color = count_style;
        } else {
            clearInterval(interval); // Stop if element is gone
        }
    }, 1000);
    event_timer_ids[element] = interval;
}