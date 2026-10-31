package web

// Added by Silo DSM (overlay/apply.py): fixes for Silo.1 until upstream has
// them, and the root paths of TorrServer's API.

import (
	"encoding/hex"
	"net/http"

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
