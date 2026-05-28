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
    var labId = $(this).attr('id');
    console.log(labId);
    cloudLog('info', 'Lab selected: ' + labId, { source: 'script', action: 'lab_selected' });

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
    if (notification) {
      notification.textContent = message;
      notification.style.display = "block";
    }
  }

  function hideNotification() {
    if (notification) {
      notification.style.display = "none";
    }
  }

  function getSelectedOptions(selectId) {
    return Array.from(
      document.querySelectorAll(`#${selectId} input:checked`)
    ).map((checkbox) => checkbox.value);
  }

  function displayOutput() {
    if (!rangeSlider || !output) return;

    const latency = document.querySelector(
      'input[name="latencyRadio"]:checked'
    ).value;
    const selected = getSelectedOptions("multiSelect");
    const sliderVal = rangeSlider.value;
    let outputHtml = "<h4>your request is in process</h4>";
    output.innerHTML = outputHtml;
    $.post({
      url: "/tools",
      data: JSON.stringify({
        changeLatency: latency === 'enable' ? true : false,
        devices: selected,
        score: sliderVal
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
    if (!configOutput) return;

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
        let outputHtml = "<h2>Configuration for Selected Devices:</h2>";
        outputHtml +=
          "<p><strong>  Result :</strong> " + response['result'].replace(/\n/g, '<br>') + "</p>";
        configOutput.innerHTML = outputHtml;
      })
      .fail(function (jqXHR, textStatus, errorThrown) {
        console.error("Error:", textStatus, errorThrown);
        let outputHtml = "<h4>Something went wrong, Try again.</h4>";
        configOutput.innerHTML = outputHtml;
      });

  }

  // Only attach latency form handlers if elements exist (tools page only)
  if (enableLatency && disableLatency && sliderContainer && rangeSlider) {
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
  }

  if (latencyForm) {
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
  }

  if (configForm) {
    configForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (getSelectedOptions("deviceSelect").length === 0) {
        configOutput.innerHTML =
          '<div class="alert alert-danger">Please select at least one device.</div>';
        return;
      }
      displayConfigOutput();
    });
  }
});

document.addEventListener("DOMContentLoaded", function () {
  var simpleLi = document.getElementById('labguides-simple');
  var expandableLi = document.getElementById('labguides-expandable');

  if (!simpleLi || !expandableLi) return;

  var simpleLink = simpleLi.querySelector('.site-sidebar__item');
  if (simpleLink) {
    simpleLink.addEventListener('click', function () {
      cloudLog('info', 'Lab guide opened', { source: 'script', action: 'labguide_open' });
    });
  }

  function initLabguidesSubMenu() {
    if (!window.featureFlags) return;
    window.featureFlags.check('labguide_pdf_download').then(function (pdfEnabled) {
      if (!pdfEnabled) return;

      simpleLi.style.display = 'none';
      expandableLi.style.display = '';

      var toggle = document.getElementById('labguidesToggle');
      var arrow = document.getElementById('labguidesArrow');
      var submenu = document.getElementById('labguidesSubmenu');

      toggle.addEventListener('click', function (e) {
        e.preventDefault();
        submenu.classList.toggle('expanded');
        arrow.classList.toggle('expanded');
      });

      var labguideLink = submenu.querySelector('.site-sidebar__subitem');
      if (labguideLink) {
        labguideLink.addEventListener('click', function () {
          cloudLog('info', 'Lab guide opened', { source: 'script', action: 'labguide_open', menu: 'submenu' });
        });
      }

      checkPdfAvailability();
    });
  }

  function checkPdfAvailability() {
    var link = document.getElementById('pdfDownloadLink');
    var item = document.getElementById('pdfDownloadItem');
    var textSpan = document.getElementById('pdfDownloadText');
    if (!link || !item || !textSpan) return;

    var attempts = 0;
    var maxAttempts = 40;
    var intervalMs = 15000;
    var pollTimer = null;

    var pdfConfirmOpen = false;

    function getDownloadFilename() {
      var title = link.getAttribute('data-topo-title');
      if (title) {
        return title.replace(/[^a-zA-Z0-9 _-]/g, '').replace(/\s+/g, '-').toLowerCase() + '-labguide.pdf';
      }
      return 'labguide.pdf';
    }

    function showPdfConfirm(href) {
      if (pdfConfirmOpen) return;
      pdfConfirmOpen = true;

      var overlay = document.createElement('div');
      overlay.className = 'pdf-confirm-overlay';

      var panel = document.createElement('div');
      panel.className = 'pdf-confirm-panel';
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-modal', 'true');
      panel.setAttribute('aria-label', 'PDF download confirmation');

      var heading = document.createElement('h3');
      heading.textContent = 'Before you download';

      var message = document.createElement('p');
      message.textContent = 'This PDF is a snapshot of the lab guide as it exists right now. ' +
        'We\'re always improving our labs, so if you want the latest version ' +
        'in the future, just come back and download it again.';

      var actions = document.createElement('div');
      actions.className = 'pdf-confirm-actions';

      var cancelBtn = document.createElement('button');
      cancelBtn.className = 'pdf-confirm-cancel';
      cancelBtn.textContent = 'Cancel';

      var downloadBtn = document.createElement('button');
      downloadBtn.className = 'pdf-confirm-download';
      downloadBtn.textContent = 'Download';

      actions.appendChild(cancelBtn);
      actions.appendChild(downloadBtn);
      panel.appendChild(heading);
      panel.appendChild(message);
      panel.appendChild(actions);
      overlay.appendChild(panel);
      document.body.appendChild(overlay);

      requestAnimationFrame(function () {
        overlay.classList.add('visible');
        downloadBtn.focus();
      });

      function onKeydown(e) {
        if (e.key === 'Escape') {
          close();
          return;
        }
        if (e.key === 'Tab') {
          var focusable = [cancelBtn, downloadBtn];
          var idx = focusable.indexOf(document.activeElement);
          if (e.shiftKey) {
            idx = idx <= 0 ? focusable.length - 1 : idx - 1;
          } else {
            idx = idx >= focusable.length - 1 ? 0 : idx + 1;
          }
          focusable[idx].focus();
          e.preventDefault();
        }
      }

      document.addEventListener('keydown', onKeydown);

      function close() {
        document.removeEventListener('keydown', onKeydown);
        overlay.classList.remove('visible');
        setTimeout(function () {
          overlay.remove();
          pdfConfirmOpen = false;
        }, 200);
      }

      cancelBtn.addEventListener('click', close);
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close();
      });
      downloadBtn.addEventListener('click', function () {
        cloudLog('info', 'PDF lab guide downloaded', { source: 'script', action: 'pdf_download' });
        var a = document.createElement('a');
        a.href = href;
        a.download = getDownloadFilename();
        document.body.appendChild(a);
        a.click();
        a.remove();
        close();
      });
    }

    function onPdfReady() {
      link.classList.remove('pdf-building');
      var spinner = link.querySelector('.pdf-spinner');
      if (spinner) spinner.remove();
      textSpan.textContent = 'Download PDF';
      link.removeAttribute('href');
      link.style.cursor = 'pointer';
      link.addEventListener('click', function (e) {
        e.preventDefault();
        showPdfConfirm('/labguides/labguide.pdf');
      });
    }

    function onPdfTimeout() {
      item.style.display = 'none';
    }

    function startPolling() {
      pollTimer = setInterval(function () {
        pollCheck();
      }, intervalMs);
    }

    function pollCheck() {
      fetch('/labguides/labguide.pdf', { method: 'HEAD' })
        .then(function (response) {
          if (response.ok) {
            if (pollTimer) {
              clearInterval(pollTimer);
              pollTimer = null;
            }
            onPdfReady();
          } else {
            attempts++;
            if (attempts >= maxAttempts && pollTimer) {
              clearInterval(pollTimer);
              pollTimer = null;
              onPdfTimeout();
            }
          }
        })
        .catch(function () {
          attempts++;
          if (attempts >= maxAttempts && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
            onPdfTimeout();
          }
        });
    }

    fetch('/labguides/labguide.pdf', { method: 'HEAD' })
      .then(function (response) {
        if (response.ok) {
          onPdfReady();
        } else {
          startPolling();
        }
      })
      .catch(function () {
        startPolling();
      });
  }

  initLabguidesSubMenu();
});
