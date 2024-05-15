const labStaustext = document.getElementById("labStatusByApi");
labStaustext.innerHTML = "<td>Please wait, Lab Status is being loaded...</td>";
let failedSwitches = []
const checkIsAllSwitchesOK = localStorage.getItem('isAllSwitchesOk')
isAllSwitchesOk = true;
if (checkIsAllSwitchesOK == 'yes' || checkIsAllSwitchesOK == undefined) {
    isAllSwitchesOk = true;
} else {
    isAllSwitchesOk = false;
}
resetRequestSubmittedTime = localStorage.getItem('resetRequestSubmittedTime')
if (resetRequestSubmittedTime) {
    document.getElementById('resetOkMSG').innerHTML = "Please wait, reset request has been submitted at " + resetRequestSubmittedTime
}

// $('#labMenu').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'block'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'none'
//     clearInterval(labStatusInterval)
// });
// $('#home').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'block'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'none'
//     clearInterval(labStatusInterval)
// })
// $('#labStaus').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'block'
//     document.getElementById('labGradingData').style.display = 'none'
//     getLabStatus()
//     labStatusInterval = setInterval(
//         () => {
//             getLabStatus()
//         }, 30000
//     )
// })
// $('#labGrading').click(function (event) {
//     document.getElementById('lab-menu').style.display = 'none'
//     document.getElementById('mainContent').style.display = 'none'
//     document.getElementById('labStatusContent').style.display = 'none'
//     document.getElementById('labGradingData').style.display = 'block'
//     clearInterval(labStatusInterval)
// })

$('#resetLabs').click((event) => {
    if (confirm('Please click ok to reset below switches \n-' + failedSwitches.join('\n-'))) {
        document.getElementById('resetLabs').innerHTML = ''
        document.getElementById('resetOkMSG').innerHTML = "Please wait, reset request has been submitted at " + new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString()
        isAllSwitchesOk = false;
        localStorage.setItem('isAllSwitchesOk', 'no')
        localStorage.setItem('resetRequestSubmittedTime', new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString())
        $.get('/resetLab?lab_names=' + failedSwitches.join(','), (res) => {

        })
    }
})


function getLabStatus() {
    let switchesTable = document.getElementById("labStatusByApi");


    $.get('/labStaus', (res) => {
        switchesTable.innerHTML = "";
        failedSwitches = []
        res.response.forEach(
            (item) => {
                let row = document.createElement("tr");
                let labName = document.createElement("td");
                values = item.split(',')
                labName.innerHTML = values[0];
                let labStatus = document.createElement("td");
                let spanElement = document.createElement("span");
               labStatus.appendChild(spanElement)
                spanElement.textContent = values[1];
                if (values[1].indexOf("Ok") >= 0) {
                    //labStatus.style.color = "green"
                    spanElement.classList.add("switch", "green");
                } else {
                    failedSwitches.push(values[0])
                    // labStatus.style.color = "red"
                    // labStatus.style.fontWeight = "bold"
                    spanElement.classList.add("switch", "red");
                    spanElement.textContent = values[1];
                }
                row.appendChild(labName);
                row.appendChild(labStatus);
                switchesTable.appendChild(row);
            }
        )
        if (failedSwitches.length == 0) {
            isAllSwitchesOk = true;
            localStorage.setItem('isAllSwitchesOk', 'yes')
            document.getElementById('resetOkMSG').innerHTML = ''
            localStorage.removeItem('resetRequestSubmittedTime')
        }
        if (isAllSwitchesOk && failedSwitches.length > 0) {
            document.getElementById('resetLabs').innerHTML = 'Please <span style="color: blue; text-decoration: underline; cursor: pointer;">click here</span> to reset failed switches'

        } else {
            document.getElementById('resetLabs').innerHTML = ''
        }

        document.getElementById('lastUpdated').innerHTML = "Last updated : " + new Date().toLocaleDateString() + ' ' + new Date().toLocaleTimeString()
    })

}






document.getElementById("labBtn").addEventListener("click", function () {
    const selected_lab_options = $('.lab-button.active').attr('id'); // Get the ID instead of text
    // document.getElementById('loader').style.display = 'block'
    $.get("/lab?lab_value=" + selected_lab_options, (res) => {
        console.log(res)
        if (res.response) {
            // output = ''
            // res.response.forEach(element => {
            //     output = output + element
            // });
            document.getElementById('apiResponse').textContent = res.response
            // document.getElementById('loader').style.display = 'none'

        }
    }).fail((err) => {
        console.log(err)
        document.getElementById('apiResponse').textContent = "Some thing went wrong"
        // document.getElementById('loader').style.display = 'none'
    })


});
function displayToolTip() {
    document.getElementById('tooltiptext').style.visibility = "visible";
}
function hideToolTip() {
    document.getElementById('tooltiptext').style.visibility = "hidden";
}
document.getElementById('CvpStatus').style.color = "grey";
document.getElementById('CvpStatus').style.pointerEvents = "none";



// For Popup

// Get the modal
var modal = document.getElementById("myModal");

// Get the button that opens the modal
var btn = document.getElementById("myBtn");

// Get the <span> element that closes the modal
var span = document.getElementsByClassName("close")[0];

// When the user clicks the button, open the modal 
btn.onclick = function () {
    modal.style.display = "block";
}

// When the user clicks on <span> (x), close the modal
span.onclick = function () {
    modal.style.display = "none";
}

// When the user clicks anywhere outside of the modal, close it
window.onclick = function (event) {
    if (event.target == modal) {
        modal.style.display = "none";
    }
}    