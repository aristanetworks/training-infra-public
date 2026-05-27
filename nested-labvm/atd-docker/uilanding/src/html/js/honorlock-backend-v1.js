import { getUserDetails } from './honorlock-common.js';
// ...existing code...
async function getExamInstruction() {
    try {
        const myHeaders = new Headers();
        const token = sessionStorage.getItem('token'); // Always assign before use
        if (!token) {
            throw new Error('Token not found in session storage');
        }
        myHeaders.append("Authorization", "Bearer " + token);
        myHeaders.append("Content-Type", "application/json");

        const uservalue = await getUserDetails();
        const raw = JSON.stringify({
            "external_exam_id": uservalue.external_exam_id,
        });

        const requestOptions = {
            method: "POST",
            headers: myHeaders,
            body: raw,
        };

        const response = await fetch("/getExamInstructions", requestOptions);
        const result = await response.json();

        console.log(result.data);
        const iframe = document.createElement('iframe');
        iframe.id = 'exam-instructions-frame';
        iframe.src = result.data.launch_screen_url;
        iframe.style.width = '100%';
        iframe.style.height = '100vh';
        document.body.appendChild(iframe);

        const honerIframe = document.getElementById('honer-iframe');
        if (honerIframe) {
            honerIframe.style.display = 'none';
        }

        await setSessionSetup();
    } catch (error) {
        console.error('Error in getExamInstruction:', error);
        // Handle the error appropriately
    }
}
async function setSessionSetup() {
    try {
        const myHeaders = new Headers();
        const token = sessionStorage.getItem('token');
        if (!token) {
            throw new Error('Token not found in session storage');
        }
        myHeaders.append("Authorization", "Bearer " + token);
        myHeaders.append("Content-Type", "application/json");

        const uservalue = await getUserDetails();
        const raw = JSON.stringify(uservalue);

        const requestOptions = {
            method: "POST",
            headers: myHeaders,
            redirect: "follow",
            body: raw,
        };

        const response = await fetch("/getUserSessionId", requestOptions);
        if (response.status === 200) {
            console.log('Conflict detected 200 redirected while creating session, redirecting to /exam-redo');
            // window.location.href = '/exam-redo';
            // return;
        }
        const result = await response.json();
        console.log('getUserSessionId result:', result);
        if (!result || !result.data) {
            window.location.reload();
            throw new Error('No data returned from /getUserSessionId');
        }

        const sessionData = await Honorlock.setupSession({
            session: result.data,
            app_url: "http://127.0.0.1:5000",
            external_exam_id: uservalue.external_exam_id,
            exam_taker_id: uservalue.exam_taker_id,
            exam_taker_name: uservalue.exam_taker_full_name,
            exam_taker_attempt_id: uservalue.exam_taker_attempt_id,
        });

        console.log('Session has been setup', sessionData);

        // Setup iframe resize handler
        Honorlock.onLaunchProctoringIframeResize((launchdata) => {
            console.log('Entered Proctoring function');
            const updatedIframeHeight = launchdata.launch_proctoring_data.iframe_height;
            const iframe = document.getElementById('launch-proctoring-iframe');
            if (iframe) {
                // iframe.style.height = updatedIframeHeight + 'px';
            }
        });

        // Setup begin exam handler
        Honorlock.onBeginExam(async () => {
            try {
                // Handle begin exam
                const token = sessionStorage.getItem('token');
                if (!token) {
                    throw new Error('Token not found in session storage');
                }
                // Function to call beginExam endpoint
                const beginExamResponse = await fetch('/beginExam', {
                    method: 'POST',
                    headers: {
                        'Authorization': 'Bearer ' + token,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        external_exam_id: uservalue.external_exam_id,
                        exam_taker_attempt_id: uservalue.exam_taker_attempt_id,
                        exam_taker_id: uservalue.exam_taker_id
                    })
                });
                if (beginExamResponse.status === 409) {
                    window.location.href = '/exam-redo';
                    return;
                }
                const beginExamData = await beginExamResponse.json();
                console.log('Conflict data from beginExam:', beginExamData);
                if (beginExamData.data && beginExamData.data.event_type === 'Continue') {
                    console.log('Handling Continue event from beginExam');
                    console.log(`Exam status: Continue - Exam taker: ${beginExamData.data.exam_taker_name || 'Unknown'}`);
                    console.log(`Exam continuation detected at: ${beginExamData.data.created_at || 'Unknown time'}`);

                    // You can also display this information to the user if needed
                    // alert(`Continuing existing exam session for ${beginExamData.data.exam_taker_name}`);
                }
                console.log('BeginExamHandler response:', beginExamData);

                console.log('Exam has begun');
                cloudLog('info', 'Exam begun', { source: 'honorlock', action: 'exam_begin' });

                // Hide instructions iframe
                const instructionsFrame = document.getElementById('exam-instructions-frame');
                if (instructionsFrame) {
                    instructionsFrame.style.display = 'none';
                }

                // Load exam
                Honorlock.examLoaded();

                // Create and setup exam iframe
                const examIframe = document.createElement('iframe');

                // Get base URL
                const baseUrlResponse = await fetch('/baseUrl', {
                    method: 'GET',
                    headers: {
                        'Authorization': 'Bearer ' + token,
                        'Content-Type': 'application/json'
                    }
                });

                const baseUrlData = await baseUrlResponse.json();
                console.log('BaseUrl response:', baseUrlData);

                if (baseUrlData.response) {
                    examIframe.src = window.location.origin + '?auth=' + baseUrlData.response + '&honorlock=true';
                    console.log(examIframe.src);
                } else {
                    throw new Error('BaseUrl response is missing the "response" field.');
                }

                // Style the exam iframe
                examIframe.style.position = "fixed";
                examIframe.style.top = "0";
                examIframe.style.left = "0";
                examIframe.style.width = "100%";
                examIframe.style.height = "100%";
                document.body.appendChild(examIframe);

                // // Create and setup submit button
                // const submitButton = document.createElement('button');
                // submitButton.textContent = "Submit Exam";
                // submitButton.id = "submitButton";

                // submitButton.addEventListener('click', async () => {
                //     try {
                //         submitButton.disabled = true; // Prevent double submission
                //         const token = sessionStorage.getItem('token');
                //         if (!token) {
                //             throw new Error('Token not found in session storage');
                //         }
                //         const endExamResponse = await fetch('/endExam', {
                //             method: 'POST',
                //             headers: {
                //                 'Authorization': 'Bearer ' + token,
                //                 'Content-Type': 'application/json'
                //             },
                //             body: JSON.stringify({
                //                 external_exam_id: uservalue.external_exam_id,
                //                 exam_taker_id: uservalue.exam_taker_id,
                //                 exam_taker_attempt_id: uservalue.exam_taker_attempt_id
                //             })
                //         });

                //         const endExamData = await endExamResponse.json();
                //         console.log('EndExamHandler response:', endExamData);
                //         if (!endExamData || endExamData.error) {
                //             alert("Error submitting exam: " + (endExamData.error || "Unknown error"));
                //             submitButton.disabled = false;
                //             return;
                //         }

                //         alert("Exam submitted successfully!");
                //         Honorlock.onExamSubmit(() => {
                //             console.log('Exam submitted');
                //             const examSubmittedMessage = document.createElement('div');
                //             examSubmittedMessage.textContent = "Exam submitted successfully!";
                //             examSubmittedMessage.style.fontSize = "20px";
                //             examSubmittedMessage.style.marginTop = "20px";
                //             document.body.appendChild(examSubmittedMessage);
                //         });
                //         Honorlock.examSubmit();
                //     } catch (error) {
                //         console.error('Error submitting exam:', error);
                //         alert("Error submitting exam. Please try again.");
                //         submitButton.disabled = false;
                //     }
                // });

                // document.body.appendChild(submitButton);
                Honorlock.questionLoaded();
            } catch (error) {
                console.error('Error in begin exam handler:', error);
                alert("Error starting exam. Please refresh and try again.");
            }
        });

    } catch (error) {
        console.error('Error in setSessionSetup:', error);
        alert("Error setting up exam session. Please refresh and try again.");
        throw error; // Re-throw to be handled by calling function
    }
}


async function initializeHonorlock() {

    try {
        const response = await fetch('/getClientId');
        const data = await response.json();
        console.log(data);
        console.log('Bearer token:', data.data.access_token);
        sessionStorage.setItem('token', data.data.access_token);
        await getExamInstruction();
    } catch (error) {
        console.error('Error fetching bearer token:', error);
    }

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
