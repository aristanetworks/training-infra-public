import { getUserDetails } from './honorlock-common.js';

// Register the callback ONCE, outside the button handler
Honorlock.onExamSubmit(() => {
    console.log('onExamSubmit callback executed');
    const examSubmittedMessage = document.createElement('div');
    examSubmittedMessage.textContent = "Exam submitted successfully!";
    examSubmittedMessage.style.fontSize = "20px";
    examSubmittedMessage.style.marginTop = "20px";
    document.body.appendChild(examSubmittedMessage);
    console.log('Exam submitted');
    // Print the JSON body sent to /endExam
    // Note: userDetails is not available here, so log a generic message
    console.log('Exam submission callback triggered.');
});

submitButton.addEventListener('click', async () => {
    try {
        const userDetails = await getUserDetails();
        fetch('/endExam', {
            method: 'POST',
            headers: {
                'Authorization': 'Bearer ' + sessionStorage.getItem('token'),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                external_exam_id: userDetails.external_exam_id,
                exam_taker_id: userDetails.exam_taker_id,
                exam_taker_attempt_id: userDetails.exam_taker_attempt_id
            })
        })
        .then(response => response.json())
        .then(data => {
            alert("Exam submitted successfully!");
            // Now trigger the SDK event, which will run the callback above
            Honorlock.examSubmit();
            console.log('EndExamHandler response:', data);
            // Print the JSON body sent to /endExam
            console.log('Submitted JSON:', JSON.stringify({
                external_exam_id: userDetails.external_exam_id,
                exam_taker_id: userDetails.exam_taker_id,
                exam_taker_attempt_id: userDetails.exam_taker_attempt_id
            }));
        })
        .catch(error => {
            console.error('Error in EndExamHandler fetch:', error);
            alert("There was an error submitting the exam. Please try again.");
        });
    } catch (error) {
        console.error('Error during exam submission:', error);
        alert("There was an error submitting the exam. Please try again.");
    }
});