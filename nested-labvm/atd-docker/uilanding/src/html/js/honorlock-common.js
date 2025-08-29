// Common functions shared between Honorlock modules
export async function getUserDetails() {
    try {
        const response = await fetch('/getAccessInfo', {
            method: 'GET',
            headers: {
                'Authorization': 'Bearer ' + sessionStorage.getItem('token'),
                'Content-Type': 'application/json'
            }
        });
        const data = await response.json();
        return {
            "exam_taker_id": data.customer_details?.exam_taker_id || "default-id",
            "exam_taker_email": (data.customer_details?.exam_taker_email || "").trim(),
            "exam_taker_full_name": data.customer_details?.exam_taker_full_name || "Unknown User",
            "external_exam_id": data.customer_details?.external_exam_id || "default-exam-id",
            "exam_taker_attempt_id": data.customer_details?.exam_taker_attempt_id || "1"
        };
    } catch (error) {
        console.error('Error fetching user details:', error);
        return {
            "exam_taker_id": "test-taker-id5",
            "exam_taker_email": "testuser5@gmail.com",
            "exam_taker_full_name": "Test user five",
            "external_exam_id": "test-course-april_external_id",
            "exam_taker_attempt_id": "2"
        };
    }
}