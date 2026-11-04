package rtc

import "testing"

func TestMediaACKRequiresCompletedTransportShutdown(t *testing.T) {
	p := &ParticipantImpl{}
	p.isClosed.Store(true)
	if p.MediaCloseConfirmed() {
		t.Fatal("IsClosed alone must never produce ACK")
	}
	p.TransportManager = &TransportManager{publisher: &PCTransport{}}
	p.mediaCloseComplete.Store(true)
	if p.MediaCloseConfirmed() {
		t.Fatal("completed callback without transport proof")
	}
	p.TransportManager.publisher.mediaClosed.Store(true)
	if !p.MediaCloseConfirmed() {
		t.Fatal("completed close should be observable")
	}
}
