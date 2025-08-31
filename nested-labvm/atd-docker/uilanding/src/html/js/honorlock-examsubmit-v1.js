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
            Honorlock.onExamSubmit(() => {
                console.log('Exam submitted');
                const examSubmittedMessage = document.createElement('div');
                examSubmittedMessage.textContent = "Exam submitted successfully!";
                examSubmittedMessage.style.fontSize = "20px";
                examSubmittedMessage.style.marginTop = "20px";
                document.body.appendChild(examSubmittedMessage);
            });
            Honorlock.examSubmit();
            console.log('EndExamHandler response:', data);
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