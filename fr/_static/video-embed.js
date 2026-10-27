// YouTube refuses to play an embedded video for a page that sends no referrer ("Error 153"), and a
// page opened as a local file sends none. The plugin's Help opens the copy of the documentation
// built on this computer first, so there each player is replaced by the thumbnail of its video,
// linked to the video on YouTube. On axqua.eu and Read the Docs the players stay as they are.
(function () {
  if (window.location.protocol !== "file:") return;
  function replacePlayers() {
    document.querySelectorAll(".video-embed iframe").forEach(function (frame) {
      var match = /\/embed\/([\w-]+)/.exec(frame.getAttribute("src") || "");
      if (!match) return;
      var link = document.createElement("a");
      link.className = "video-embed-link";
      link.href = "https://youtu.be/" + match[1];
      link.target = "_blank";
      link.rel = "noopener";
      link.title = frame.getAttribute("title") || "YouTube";
      var image = document.createElement("img");
      image.src = "https://i.ytimg.com/vi/" + match[1] + "/hqdefault.jpg";
      image.alt = link.title;
      var play = document.createElement("span");
      play.className = "video-embed-play";
      play.setAttribute("aria-hidden", "true");
      link.appendChild(image);
      link.appendChild(play);
      frame.replaceWith(link);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", replacePlayers);
  } else {
    replacePlayers();
  }
})();
