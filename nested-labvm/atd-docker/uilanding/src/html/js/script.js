$(document).ready(function () {
  $(".menu-icon,.menu-click").click(function () {
    $(".left-sidebar").toggleClass("active");
  });
  $(".topology").click(function () {
    $("#dashboard").hide();
    $("#main").show();
  });
  $(".menu-click").click(function () {

    $id = $(this).data("id");
    $id.addClass("current-page");
    if ($id == "lab-status" || $id == "lab-menu") {
      $(".panel").hide();
      
    } else {
      $(".panel").show();
    }
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
    $("#" + $id).show();
    
  });
  $(document).foundation();
});