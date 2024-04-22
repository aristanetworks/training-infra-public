$(document).ready(function () {
  $(".menu-icon,.menu-click").click(function () {
    $(".left-sidebar").toggleClass("active");
  });
  $(".topology").click(function () {
    $("#dashboard").hide();
    $("#main").show();
  });
  $(".menu-click").click(function () {
    $(".panel").hide();
    $id = $(this).data("id");
    $("#" + $id).show();
  });
  $(document).foundation();
});
