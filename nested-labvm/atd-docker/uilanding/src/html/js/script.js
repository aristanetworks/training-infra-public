labStatusInterval = null

$(document).ready(function () {
  $(".menu-icon,.menu-click").click(function () {
    $(".left-sidebar").toggleClass("active");
  });
  $(".topology").click(function () {
    $("#dashboard").hide();
    $("#main").show();
  });
  $(".menu-click").click(function () {
    var elements = document.getElementsByClassName('menu-click');

    for (var i = 0; i < elements.length; i++) {
      elements[i].classList.remove('current-page');
    }
    $id = $(this).data("id");
    $(this).addClass("current-page");
    if ($id == "lab-status" || $id == "lab-menu" || $id == "tools-div") {
      $(".panel").hide();

    } else {
      $(".panel").show();
      $('#lab-status').hide()
      $('#lab-menu').hide()


    }
    $("#" + $id).show();
    if ($id == "lab-status") {
      getLabStatus()
      labStatusInterval = setInterval(
        () => {
          getLabStatus()
        }, 30000
      )
    } else {
      clearInterval(labStatusInterval)
    }
  });
  $(document).foundation();
});



$(function () {
  $('.lab-button').click(function () {
    // Remove 'active' class from all buttons
    $('.lab-button').removeClass('active');

    // Add 'active' class to the clicked button
    $(this).addClass('active');

    // Print the button's text to the console
    console.log($(this).attr('id'));

    // Prevent default action if it's an anchor tag
    return false;
  });
});



document.addEventListener("DOMContentLoaded", function () {
  const latencyForm = document.getElementById("latencyForm");
  const configForm = document.getElementById("configForm");
  const enableLatency = document.getElementById("enableLatency");
  const disableLatency = document.getElementById("disableLatency");
  const sliderContainer = document.getElementById("sliderContainer");
  const rangeSlider = document.getElementById("rangeSlider");
  const sliderValue = document.getElementById("sliderValue");
  const notification = document.getElementById("notification");
  const output = document.getElementById("output");
  const configOutput = document.getElementById("configOutput");

  function showNotification(message) {
    notification.textContent = message;
    notification.style.display = "block";
  }

  function hideNotification() {
    notification.style.display = "none";
  }

  function getSelectedOptions(selectId) {
    return Array.from(
      document.querySelectorAll(`#${selectId} input:checked`)
    ).map((checkbox) => checkbox.value);
  }

  function displayOutput() {

    const latency = document.querySelector(
      'input[name="latencyRadio"]:checked'
    ).value;
    const selected = getSelectedOptions("multiSelect");
    const sliderValue = rangeSlider.value;
    let outputHtml = "<h4>your request is in process</h4>";
    output.innerHTML = outputHtml;
    $.post({
      url: "/tools",
      data: JSON.stringify({
        changeLatency: latency === 'enable' ? true : false,
        devices: selected,
        score: sliderValue
      }),
      contentType: "application/json",
      dataType: "json"
    })
      .done(function (response) {

        let outputHtml = "<h2>Latency Change Results:</h2>";
        //let outputHtml = "<p><strong>Latency:</strong> " + response['changeLatency'] ? 'Enable' : 'Disable' + "</p>";
        outputHtml +=
          "<p><strong>Selected Devices:</strong> " +
          response['devices'].join(", ") +
          "</p>";
        if (latency === "enable") {
          outputHtml +=
            "<p><strong>Latency Value:</strong> " + response['score'] + " ms</p>";
        }
        outputHtml += "<p><strong>Result:</strong> " + response['result'].replace(/\n/g, '<br>') + "</p>";

        output.innerHTML = outputHtml;
      })
      .fail(function (jqXHR, textStatus, errorThrown) {
        console.error("Error:", textStatus, errorThrown);

        let outputHtml = "<h4>Something went wrong, Try again.</h4>";
        output.innerHTML = outputHtml;
      });


  }

  function displayConfigOutput() {
    const selectedDevices = getSelectedOptions("deviceSelect");
    let outputHtml = "<h4>your request is in process</h4>";
    configOutput.innerHTML = outputHtml;

    $.post({
      url: "/viewConfig",
      data: JSON.stringify({

        devices: selectedDevices,

      }),
      contentType: "application/json",
      dataType: "json"
    })
      .done(function (response) {
        console.log("Success:", response);
        let outputHtml = "<h4>Configuration for Selected Devices:</h4>";
        outputHtml +=
          "<p><strong>  Result :</strong> " + response['result'] + "</p>";
        configOutput.innerHTML = outputHtml;
      })
      .fail(function (jqXHR, textStatus, errorThrown) {
        console.error("Error:", textStatus, errorThrown);
        let outputHtml = "<h4>Something went wrong, Try again.</h4>";
        configOutput.innerHTML = outputHtml;
      });

  }

  enableLatency.addEventListener("change", function () {
    sliderContainer.style.display = this.checked ? "block" : "none";
    rangeSlider.required = this.checked;
  });

  disableLatency.addEventListener("change", function () {
    sliderContainer.style.display = "none";
    rangeSlider.required = false;
  });

  rangeSlider.addEventListener("input", function () {
    sliderValue.textContent = this.value;
  });

  latencyForm.addEventListener("submit", function (e) {
    e.preventDefault();
    hideNotification();
    output.innerHTML = "";

    if (!document.querySelector('input[name="latencyRadio"]:checked')) {
      showNotification("Please select a latency option.");
      return;
    }

    if (getSelectedOptions("multiSelect").length === 0) {
      showNotification(
        "Please select at least one option from the multiselect."
      );
      return;
    }

    if (enableLatency.checked && !rangeSlider.value) {
      showNotification("Please set a value for the slider.");
      return;
    }

    displayOutput();
  });

  configForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (getSelectedOptions("deviceSelect").length === 0) {
      configOutput.innerHTML =
        '<div class="alert alert-danger">Please select at least one device.</div>';
      return;
    }
    displayConfigOutput();
  });
});


