import { getUserDetails } from './honorlock-common.js';

const submitButton = document.getElementById('submitButton');

submitButton.addEventListener('click', async () => {
    try {
        const userDetails = await getUserDetails();

    alert("Exam submitted successfully!");
    Honorlock.onExamSubmit(() => {
        console.log('Exam submitted');
        const examSubmittedMessage = document.createElement('div');
        examSubmittedMessage.textContent = "Exam submitted successfully!";
        examSubmittedMessage.style.fontSize = "20px";
        examSubmittedMessage.style.marginTop = "20px";
        document.body.appendChild(examSubmittedMessage);
    });
    fetch('/endExam', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + sessionStorage.getItem('token'),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            external_exam_id: "test-course-april_external_id",
            exam_taker_id: "test-taker-id5",
            exam_taker_attempt_id: "2"
        })
    })
    .then(response => response.json())
    .then(data => {
        console.log('EndExamHandler response:', data);
    })
    .catch(error => {
        console.error('Error in EndExamHandler fetch:', error);
    });

    Honorlock.examSubmit();
    } catch (error) {
        console.error('Error during exam submission:', error);
        alert("There was an error submitting the exam. Please try again.");
    }
});

