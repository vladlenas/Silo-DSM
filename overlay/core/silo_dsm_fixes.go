package web

// Added by Silo DSM (overlay/apply.py): fixes for Silo.1 until upstream has them.

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
