package pull

import "sync"

// layerState theo dõi tiến độ một layer (một Event.LayerID) của một image.
type layerState struct {
	current, total int64
}

// Aggregator gộp Event của nhiều image tải song song thành % tổng hợp theo
// BYTE THẬT — an toàn gọi đồng thời từ nhiều goroutine (mỗi image một
// goroutine gọi Puller.Pull riêng).
type Aggregator struct {
	mu      sync.Mutex
	images  []string                          // thứ tự hiển thị cố định, theo đúng thứ tự truyền vào NewAggregator
	layers  map[string]map[string]*layerState // image -> layerID -> state
	errored map[string]string                 // image -> thông điệp lỗi (từ Event.ErrMsg)
}

// NewAggregator dựng Aggregator theo dõi đúng danh sách images cho trước —
// cố định thứ tự hiển thị dù các goroutine tải xong không theo thứ tự đó.
func NewAggregator(images []string) *Aggregator {
	a := &Aggregator{
		images:  append([]string(nil), images...),
		layers:  make(map[string]map[string]*layerState, len(images)),
		errored: make(map[string]string),
	}
	for _, img := range images {
		a.layers[img] = make(map[string]*layerState)
	}
	return a
}

// Apply cập nhật trạng thái theo một Event — an toàn gọi đồng thời.
func (a *Aggregator) Apply(ev Event) {
	a.mu.Lock()
	defer a.mu.Unlock()

	if ev.ErrMsg != "" {
		a.errored[ev.Image] = ev.ErrMsg
		return
	}

	layers, ok := a.layers[ev.Image]
	if !ok {
		layers = make(map[string]*layerState)
		a.layers[ev.Image] = layers
	}

	switch ev.Status {
	case "Already exists":
		// Layer đã có sẵn cục bộ — Docker không tải lại nên không có byte
		// thật để đếm; tính coi như xong ngay với trọng số tối thiểu (1/1)
		// để không kéo % tổng xuống 0 khi TOÀN BỘ layer của một image đều
		// đã có sẵn (ví dụ chạy `genh install` lần hai).
		if _, exists := layers[ev.LayerID]; !exists {
			layers[ev.LayerID] = &layerState{current: 1, total: 1}
		}
	case "Downloading":
		st, exists := layers[ev.LayerID]
		if !exists {
			st = &layerState{}
			layers[ev.LayerID] = st
		}
		st.current, st.total = ev.Current, ev.Total
	case "Extracting":
		st, exists := layers[ev.LayerID]
		if !exists {
			st = &layerState{}
			layers[ev.LayerID] = st
		}
		if ev.Total > st.total {
			st.total = ev.Total
		}
		if ev.Current > st.current {
			st.current = ev.Current
		}
	case "Pull complete":
		st, exists := layers[ev.LayerID]
		if !exists {
			layers[ev.LayerID] = &layerState{current: 1, total: 1}
			return
		}
		if st.total <= 0 {
			st.total = 1
		}
		st.current = st.total
	default:
		// "Pulling fs layer", "Waiting", "Verifying Checksum", "Downloaded
		// newer image for…", "Status: Image is up to date…"… — không mang
		// byte, không cần cập nhật gì.
	}
}

// Totals trả về tổng byte đã tải/tổng byte cần tải trên MỌI image đang theo
// dõi — cơ sở của % tổng theo byte thật (yêu cầu tài liệu).
func (a *Aggregator) Totals() (current, total int64) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, layers := range a.layers {
		for _, st := range layers {
			current += st.current
			total += st.total
		}
	}
	return
}

// Percent trả % tổng 0..100 theo byte thật; 0 nếu chưa layer nào báo Total.
func (a *Aggregator) Percent() float64 {
	c, t := a.Totals()
	return percentOf(c, t)
}

// ImagePercent trả % riêng một image — dùng cho dòng con hiển thị từng
// image (mockup TUI: "api ███░░ 86%").
func (a *Aggregator) ImagePercent(image string) float64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	var c, t int64
	for _, st := range a.layers[image] {
		c += st.current
		t += st.total
	}
	return percentOf(c, t)
}

func percentOf(current, total int64) float64 {
	if total <= 0 {
		return 0
	}
	pct := float64(current) / float64(total) * 100
	if pct > 100 {
		pct = 100
	}
	if pct < 0 {
		pct = 0
	}
	return pct
}

// Images trả về danh sách image đang theo dõi, đúng thứ tự truyền vào
// NewAggregator (ổn định cho hiển thị dù thứ tự tải xong khác nhau).
func (a *Aggregator) Images() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.images...)
}

// Errors trả về bản sao map lỗi hiện có (image -> thông điệp) — rỗng nếu
// chưa image nào lỗi.
func (a *Aggregator) Errors() map[string]string {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make(map[string]string, len(a.errored))
	for k, v := range a.errored {
		out[k] = v
	}
	return out
}
