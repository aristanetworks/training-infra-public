function getExamInstruction() {
    const myHeaders = new Headers();
    token = sessionStorage.getItem('token');
    myHeaders.append("Authorization", "Bearer " + token);
    myHeaders.append("Content-Type", "application/json"); // Ensure Content-Type is set
    uservalue = {
        "external_exam_id": "test-course-april_external_id",
    }
    const raw = JSON.stringify(uservalue);

    const requestOptions = {
        method: "POST",
        headers: myHeaders,
        body: raw,
    };

    fetch("/getExamInstructions", requestOptions)
        .then((response) => response.json())
        .then((result) => {
            console.log(result.data);
            const iframe = document.createElement('iframe');
            iframe.id = 'exam-instructions-frame';
            iframe.src = result.data.launch_screen_url;
            iframe.style.width = '100%';
            iframe.style.height = '600px';
            document.body.appendChild(iframe);
            document.getElementById('honer-iframe').style.display = 'none';
            document.getElementById('honer-ext-reload-text').style.display = 'none';
            setSessionSetup()
        })
}
function setSessionSetup() {
    const myHeaders = new Headers();
    token = sessionStorage.getItem('token');
    myHeaders.append("Authorization", "Bearer " + token);
    myHeaders.append("Content-Type", "application/json"); // Ensure Content-Type is set
    uservalue = {
        "exam_taker_id": "test-taker-id5",
        "exam_taker_email": "testuser5@gmail.com",
        "exam_taker_full_name": "Test user five",
        "external_exam_id": "test-course-april_external_id",
        "exam_taker_attempt_id": "2"
    }
    const raw = JSON.stringify(uservalue);

    const requestOptions = {
        method: "POST",
        headers: myHeaders,
        redirect: "follow",
        body: raw,
    };

    fetch("/getUserSessionId", requestOptions)
        .then((response) => response.json())
        .then((result) => {
            console.log(result.data);
            Honorlock.setupSession({
                session: result.data,
                app_url: "http://127.0.0.1:5000",
                external_exam_id: uservalue['external_exam_id'],
                exam_taker_id: uservalue['exam_taker_id'],
                exam_taker_name: uservalue['exam_taker_full_name'],
                exam_taker_attempt_id: uservalue['exam_taker_attempt_id'],

            }).then((data) => {
                console.log('Session has been setup', data);

                Honorlock.onLaunchProctoringIframeResize((launchdata) => {
                    console.log('Entered Proctoring function');
                    let updatedIframeHeight = launchdata.launch_proctoring_data.iframe_height;
                    //platform specific code on how to get the iframe and adjust the height.
                    let iframe = document.getElementById('launch-proctoring-iframe');
                    // iframe.style.height = updatedIframeHeight + 'px';
                });

                Honorlock.onBeginExam(() => {
                    fetch('/beginExam', {
                        method: 'POST',
                        headers: {
                            'Authorization': 'Bearer ' + sessionStorage.getItem('token'),
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            external_exam_id: uservalue['external_exam_id'],
                            exam_taker_attempt_id: uservalue['exam_taker_attempt_id'],
                            exam_taker_id: uservalue['exam_taker_id']
                        })
                    })
                    .then(response => response.json())
                    .then(data => {
                        console.log('BeginExamHandler response:', data);
                        // Handle response if needed
                    })
                    .catch(error => {
                        console.error('Error in BeginExamHandler fetch:', error);
                    });
                    console.log('Exam has begun');
                    const iframe = document.getElementById('exam-instructions-frame');
                    if (iframe) {
                        iframe.style.display = 'none';
                    }
                    Honorlock.examLoaded();
                    const examStartedMessage = document.createElement('div');
                    examStartedMessage.textContent = "The exam has started. Good luck!";
                    examStartedMessage.style.fontSize = "20px";
                    examStartedMessage.style.marginTop = "20px";
                    document.body.appendChild(examStartedMessage);

                    const iframe2 = document.createElement('iframe');
                    iframe2.src = window.location.origin; // Dynamically set to the base URL of the current JS location
                    iframe2.style.position = "fixed";
                    iframe2.style.top = "0";
                    iframe2.style.left = "0";
                    iframe2.style.width = "100%";
                    iframe2.style.height = "100%";
                    iframe2.style.border = "none";
                    iframe2.style.zIndex = "9999";
                    document.body.appendChild(iframe2);

                    const submitButton = document.createElement('button');
                    submitButton.textContent = "Submit Exam";
                    submitButton.style.display = "block";
                    submitButton.style.marginTop = "20px";
                    submitButton.style.padding = "10px 20px";
                    submitButton.style.fontSize = "16px";
                    submitButton.addEventListener('click', () => {
                        alert("Exam submitted successfully!");
                        Honorlock.onExamSubmit(() => {
                            //platform specific code on how to proceed when submitting the exam
                            //e.g we submit a form on the page
                            console.log('Exam submitted');
                            const examSubmittedMessage = document.createElement('div');
                            examSubmittedMessage.textContent = "Exam submitted successfully!";
                            examSubmittedMessage.style.fontSize = "20px";
                            examSubmittedMessage.style.marginTop = "20px";
                            document.body.appendChild(examSubmittedMessage);
                            // Optionally, you can redirect the user or perform other actions here
                        });
                        // Add any additional logic for submitting the exam here
                        fetch('/endExam', {
                            method: 'POST',
                            headers: {
                                'Authorization': 'Bearer ' + sessionStorage.getItem('token'),
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                external_exam_id: uservalue['external_exam_id'],
                                exam_taker_id: uservalue['exam_taker_id'],
                                exam_taker_attempt_id: uservalue['exam_taker_attempt_id']
                            })
                        })
                        .then(response => response.json())
                        .then(data => {
                            console.log('EndExamHandler response:', data);
                            // Handle response if needed
                        })
                        .catch(error => {
                            console.error('Error in EndExamHandler fetch:', error);
                        });
                        Honorlock.examSubmit();
                    });
                    document.body.appendChild(submitButton);
                });
            }).catch((error) => console.error(error));
        });

}


function initializeHonorlock() {

    fetch('/getClientId')
        .then(response => response.json())
        .then(data => {
            console.log(data);
            console.log('Bearer token:', data.data.access_token);
            sessionStorage.setItem('token', data.data.access_token);
            // Use the token as needed

            getExamInstruction();
        })
        .catch(error => {
            console.error('Error fetching bearer token:', error);
        });

}

function getHonerLockThings() {

    Honorlock.init().then(() => {
        if (Honorlock.isInitialized == false) {
            document.getElementById('honer-iframe').style.display = 'block';
            document.getElementById('honer-ext-reload-text').style.display = 'block';

        } else {
            initializeHonorlock()
        }
    })
        .catch((error) => {
            console.error('Error initializing Honorlock:', error);
        });

    console.log(Honorlock.pageHasExtensionInstallIframe())


}



// Automatically initialize on page load (if desired)
window.addEventListener('load', () => {
    getHonerLockThings();
});
