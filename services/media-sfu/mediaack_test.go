package service

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMediaControlNeverAcceptsPublicUnsignedRequest(t *testing.T) {
	r := httptest.NewRequest("POST", "/revoke", strings.NewReader(`{"operation":"00000000-0000-0000-0000-000000000001","grants":[]}`))
	w := httptest.NewRecorder()
	mediaRevoke(w, r)
	if w.Code == 200 {
		t.Fatal("unsigned ACK request accepted")
	}
}
