package web

// Added by Silo DSM (overlay/apply.py): fixes for Silo.1 until upstream has
// them, and the root paths of TorrServer's API.

import (
	"encoding/hex"
	"net/http"
	"os"

	"silo/internal/ffmpeg/hls"
	"silo/internal/log"

	"github.com/gin-gonic/gin"
)

// siloDSMValidHash answers 400 for a :hash that is not 40 hex characters;
// a short one made the torrent handlers panic.
func siloDSMValidHash() gin.HandlerFunc {
	return func(c *gin.Context) {
		if h := c.Param("hash"); h != "" {
			if _, err := hex.DecodeString(h); len(h) != 40 || err != nil {
				c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid torrent hash"})
				return
			}
		}
		c.Next()
	}
}

// siloDSMTorrServerPaths are the root paths of TorrServer's API that apps
// (Lampa, Seena, TorrServe clients) call on "http://<server>:<port>". Silo
// has none of them; they are handed to the built-in plugin torrserver-api,
// which answers like a TorrServer and plays from Silo.
var siloDSMTorrServerPaths = []string{
	"/echo", "/settings", "/torrents", "/viewed", "/cache",
	"/stream", "/stream/*name", "/play/*rest",
}

func siloDSMTorrServerRoutes(s *Server) {
	alias := func(c *gin.Context) {
		c.Request.URL.Path = "/plugins/torrserver-api" + c.Request.URL.Path
		c.Request.URL.RawPath = ""
		s.router.HandleContext(c)
	}
	for _, p := range siloDSMTorrServerPaths {
		s.router.Any(p, alias)
	}
}

// siloDSMDropAhead removes the segments from index on before ffmpeg is
// started there; the old process is already stopped.
//
// They are left over from another place in the film (the player was there
// before a seek). ensure() takes the highest segment number in the folder for
// how far the running ffmpeg has got: with segment 330 left over, a restart
// at 120 makes the request for 121 look "already made and deleted", so ffmpeg
// is restarted at 121, then at 122, 123... and playback stops. The new
// process writes these numbers again anyway.
func (h *hlsSession) siloDSMDropAhead(s *Server, index int) {
	dir := h.workDir(s)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	dropped := 0
	for _, e := range entries {
		if e.IsDir() || !isSegmentName(e.Name()) {
			continue
		}
		if num, ok := hls.SegmentIndex(e.Name()); ok && num >= index {
			removeSegment(dir, e.Name())
			dropped++
		}
	}
	if dropped > 0 {
		log.Debugf("[Web] Silo DSM: session %s dropped %d left-over segments from %d on", h.ID, dropped, index)
	}
}
