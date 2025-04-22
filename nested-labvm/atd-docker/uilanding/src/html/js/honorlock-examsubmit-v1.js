const submitButton = document.getElementById('submitButton');

submitButton.addEventListener('click', () => {
    alert("Exam submitted successfully!");
    Honorlock.onExamSubmit(() => {
        // Platform-specific code on how to proceed when submitting the exam
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
