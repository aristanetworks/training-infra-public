var atdURL = window.location.origin;
if ( atdURL.includes('https') ) {
    atdURL = atdURL.replace("https:","wss:");
}
else {
    atdURL = atdURL.replace("http:","ws:");
}

atdURL += "/td-ws";
var ws = new WebSocket(atdURL);
var event_timer_ids = {};
var topo_notify = false;

createWS(atdURL);


function createWS(SOCK_URL) {
    // Create a websocket connection
    ws = new WebSocket(SOCK_URL);
    ws.onopen = function(){
        // Web Socket is connected, send data using send()
        ws.send(JSON.stringify({
            type:"hello",
            data: {
                action: "status"
            }
        }));
    };

    ws.onclose = function (evt) {
        if ( !evt.wasClean ) {
            setTimeout(function() {
                createWS(SOCK_URL);
            },500)
        }
    }

    ws.onmessage = function (evt) 
    {
        var re_data = evt.data;
        var received_msg = JSON.parse(re_data);
        console.log(received_msg)
        reg_data = received_msg['data'];
        if (reg_data['cvp'] && reg_data['cvp']['status'] && reg_data['cvp']['status'] != 'UP') {
            
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
                instanceCountdown('countdown_timer', uptime_data['boottime'], uptime_data['runtime']);
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
    var count_style = 'black';
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