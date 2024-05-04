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
    if ($id == "lab-status" || $id == "lab-menu") {
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



$(function() {
  $('.lab-button').click(function() {
    // Remove 'active' class from all buttons
    $('.lab-button').removeClass('active');

    // Add 'active' class to the clicked button
    $(this).addClass('active');

    // Print the button's text to the console
    console.log($(this).text().trim());

    // Prevent default action if it's an anchor tag
    return false;
  });
});
