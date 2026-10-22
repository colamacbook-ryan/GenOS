/**
 * Screen registry — docs/design/screens.json + the design's TITLES map and
 * screen-title descriptions (docs/design/Gen-Harness Console.dc.html).
 * The router, breadcrumbs and the static nav fallback are all generated
 * from this one tree (docs/handoff/04 "không khai báo hai nơi").
 * test/unit/screens.test.ts checks it against docs/design/screens.json.
 */

export type DomainId = 'business' | 'tech';

export interface ScreenMeta {
  key: string;
  domain: DomainId;
  /** Parent group name (null for level-1 screens). */
  parent: string | null;
  /** Nav label (screens.json `name`). */
  name: string;
  /** Mô tả ngắn tiếng Việt cho mục thanh bên (screens.json `en`; v0.1.42 F-63 đã Việt hoá), dùng ở tooltip. */
  en: string;
  /** Header title (TITLES[0]). */
  title: string;
  /** Header English subtitle (TITLES[1]), shown for parent-level screens only. */
  subtitle: string;
  /** Screen-title row description (12.5px neutral-400) from the design. */
  description: string;
  /** Max width of the title block in the design (700 or 760). */
  descMaxWidth: 700 | 760;
  /**
   * Whether the design draws a screen-title row for this screen. overview,
   * workbench and profile have none in the design (the header carries the
   * title), so the app draws none there either.
   */
  designTitleRow: boolean;
  /** Phosphor icon class from the design NAV. */
  icon: string;
  /**
   * Screen the spec requires but the design lacks (docs/handoff/01 "Màn còn thiếu", PLAN Q5):
   * built in the same design language, absent from docs/design/screens.json.
   */
  extra?: boolean;
  /**
   * v0.1.42 (F-65, F-41): có trong cây, có route, nhưng KHÔNG hiện trên thanh bên (GET /navigation trả
   * `hidden: true`) — Hồ sơ sống mở từ danh sách, Plugin đóng băng.
   */
  navHidden?: boolean;
  /** v0.1.42: chỉ hiện trên thanh bên khi đã có ít nhất 1 nhân viên (API ẩn khi chưa có). */
  needsStaff?: boolean;
}

export interface DomainMeta {
  id: DomainId;
  label: string;
  crumb: string;
  icon: string;
  /** v0.1.42: domain thu gọn mặc định trên thanh bên (Nâng cao). */
  collapsedByDefault: boolean;
}

export const DOMAINS: Record<DomainId, DomainMeta> = {
  business: { id: 'business', label: 'Việc hằng ngày', crumb: 'HẰNG NGÀY', icon: 'ph-fill ph-briefcase', collapsedByDefault: false },
  tech: { id: 'tech', label: 'Nâng cao', crumb: 'NÂNG CAO', icon: 'ph-fill ph-cpu', collapsedByDefault: true },
};

export const GROUP_ICONS: Record<string, string> = {
  'Hộp thư & Việc': 'ph ph-tray',
  'Khách & Cơ hội': 'ph ph-address-book',
  'Tầng dữ liệu': 'ph ph-database',
  'Agent & Model': 'ph ph-robot',
};

export const SCREENS: ScreenMeta[] = [
  {
    key: 'overview', domain: 'business', parent: null, icon: 'ph ph-sun-horizon',
    name: 'Hôm nay', en: 'Cần Sếp xử lý · 4 số chính',
    title: 'Hôm nay', subtitle: 'Cần Sếp xử lý · 4 số chính',
    description: 'Command Overview · hôm nay có gì đang xảy ra', descMaxWidth: 700, designTitleRow: false,
  },
  {
    key: 'inbox', domain: 'business', parent: 'Hộp thư & Việc', icon: 'ph ph-tray',
    name: 'Hộp thư', en: 'Tin quan trọng đã được sắp xếp',
    title: 'Hộp thư', subtitle: 'Tin quan trọng đã được sắp xếp · cái gì quan trọng nhất lúc này',
    description: 'Không phải tin nhắn thô. Mỗi dòng là một ý chính rút ra từ tin nhắn: nguồn, người liên quan, điểm ưu tiên, tóm tắt hai câu, gợi ý việc nên làm và tin gốc làm chứng cứ.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'workbench', domain: 'business', parent: 'Hộp thư & Việc', icon: 'ph ph-pen-nib',
    name: 'Bàn làm việc', en: 'Soạn & duyệt tin trả lời',
    title: 'Bàn làm việc', subtitle: 'Workbench · soạn, duyệt, hành động',
    description: 'Workbench · soạn, duyệt, hành động', descMaxWidth: 700, designTitleRow: false,
  },
  {
    key: 'tasks', domain: 'business', parent: 'Hộp thư & Việc', icon: 'ph ph-check-square', extra: true,
    name: 'Việc & Nhắc hẹn', en: 'Việc cần làm và lời nhắc',
    title: 'Việc & Nhắc hẹn', subtitle: 'Tasks & Reminders · việc đến hạn và lời hứa',
    description: 'Việc sinh ra từ lời hứa trong hội thoại, từ bản nháp đã duyệt hoặc do Sếp tạo tay. Việc quá hạn tô đỏ, lời hứa sắp đến hạn được nhắc trước để không ai bị bỏ quên.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'directory', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-address-book',
    name: 'Khách & Nhóm', en: 'Nhóm theo kênh · lọc khách',
    title: 'Khách & Nhóm', subtitle: 'Danh sách nhóm theo kênh và con người có bộ lọc',
    description: 'Danh sách nhóm tách theo từng kênh, và danh sách con người lọc được theo mức liên quan với Sếp, độ nhiệt, giá trị và mức ưu tiên — từ đó gán agent trực tương ứng.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'opportunity', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-target',
    name: 'Bảng cơ hội', en: 'Cơ hội bán hàng đang theo',
    title: 'Bảng cơ hội', subtitle: 'Opportunity Board · pipeline sống từ chat',
    description: 'Pipeline sống từ hội thoại, không phải form nhập tay. Mỗi thẻ trả lời: ai cần gì, nóng đến đâu, tin được đến đâu, nên ghép với ai, và mất gì nếu không làm gì.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'deals', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-handshake', extra: true,
    name: 'Deal & Vụ việc', en: 'Thương vụ và vụ việc',
    title: 'Deal & Vụ việc', subtitle: 'Deals & Cases · đã chốt và đang xử lý',
    description: 'Deal đã chốt từ bảng cơ hội và các vụ việc cần xử lý như khiếu nại, cảnh báo giao hàng hay thanh toán, kèm trạng thái và người đang xử lý.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'documents', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-files', extra: true,
    name: 'Tài liệu', en: 'Báo giá, hợp đồng, tệp',
    title: 'Tài liệu', subtitle: 'Documents · báo giá, hợp đồng, tệp đã trao đổi',
    description: 'Báo giá, hợp đồng, biên bản và tệp đã trao đổi trên các kênh, gắn về nhóm và người sở hữu. Ai được xem tài liệu nào do quyền truy cập của từng tài liệu quyết định.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'search', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-brain',
    name: 'Kho hội thoại', en: 'Tìm trong mọi hội thoại',
    title: 'Kho hội thoại', subtitle: 'Knowledge & Search · tìm mẫu, không chỉ tìm câu',
    description: 'Tìm theo ý định, người, ngành hàng, khoảng giá, thời gian và thái độ — không chỉ theo chữ. Mục đích là tìm ra mẫu, không chỉ tìm ra câu.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'profile', domain: 'business', parent: 'Khách & Cơ hội', icon: 'ph ph-identification-card', navHidden: true,
    name: 'Hồ sơ sống', en: 'Hồ sơ một người — mở từ danh sách',
    title: 'Hồ sơ sống', subtitle: 'Living Profile · vì sao hệ thống nghĩ vậy',
    description: 'Living Profile · vì sao hệ thống nghĩ vậy', descMaxWidth: 700, designTitleRow: false,
  },
  {
    key: 'connections', domain: 'business', parent: null, icon: 'ph ph-plugs-connected', extra: true,
    name: 'Kết nối', en: 'Bộ não AI, Zalo, Facebook, Gen-hub…',
    title: 'Kết nối', subtitle: 'Connections · bộ não AI, kênh, Facebook, Gen-hub, MCP',
    description: 'Mỗi thứ một thẻ, cùng một kiểu trạng thái: Đang chạy, Cần Sếp xử lý hoặc Chưa nối. Bấm nút chính trên thẻ để nối hoặc sửa.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'team', domain: 'business', parent: null, icon: 'ph ph-users', extra: true,
    name: 'Đội ngũ', en: 'Người dùng, đánh giá, chăm sóc',
    title: 'Đội ngũ', subtitle: 'Team · người dùng, đánh giá, chăm sóc',
    description: 'Người dùng Console: mời, đổi vai trò, khoá. Lối vào Đánh giá và Chăm sóc nhân viên (thanh bên hiện khi đã có ít nhất 1 nhân viên).',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'people', domain: 'business', parent: 'Đội ngũ', needsStaff: true, icon: 'ph ph-users-three',
    name: 'Đánh giá con người', en: 'Điểm nhân viên có chứng cứ',
    title: 'Đánh giá con người', subtitle: 'People Review · điểm số có chứng cứ',
    description: 'Điểm số là công cụ hỗ trợ quản lý, không phải bản án. Mỗi dòng có xu hướng, tín hiệu nổi bật trong tuần, khuyến nghị và nút xem chứng cứ gốc.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'care', domain: 'business', parent: 'Đội ngũ', needsStaff: true, icon: 'ph ph-heartbeat',
    name: 'Chất lượng chăm sóc', en: 'Cách nhân viên chăm khách',
    title: 'Chất lượng chăm sóc', subtitle: 'Care Quality · cách chăm, không chỉ số lần nhắn',
    description: 'Hệ thống không chỉ biết đã nhắn hay chưa, mà biết cách nhắn: follow quá sớm hay quá muộn, hứa rồi quên, quên khách sau báo giá, và kịch bản nào đang chuyển thành deal.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'system', domain: 'business', parent: null, icon: 'ph ph-gear-six',
    name: 'Cài đặt', en: 'Sao lưu, cập nhật, tổ chức, bộ não AI, quyền, nhật ký',
    title: 'Cài đặt', subtitle: 'Settings · sao lưu, cập nhật, tổ chức, bộ não AI, quyền, nhật ký',
    description: 'Cài đặt chung: sao lưu & cập nhật, tổ chức, bộ não AI, quyền hạn và nhật ký. Kênh và tài khoản nằm ở Kết nối, người dùng ở Đội ngũ.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'raw', domain: 'tech', parent: 'Tầng dữ liệu', icon: 'ph ph-database',
    name: 'Kho dữ liệu thô', en: 'Tin gốc các kênh gom về',
    title: 'Kho dữ liệu thô', subtitle: 'Raw Lake · mọi bridge gom hết về đây trước khi phân loại',
    description: 'Mọi kênh đang nghe gom hết về đây nguyên trạng, không xử lý gì. Core agent lấy từ kho này ra phân loại theo chu kỳ hoặc theo ngưỡng số lượng, rồi ghi kết quả sang kho sạch.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'listenlog', domain: 'tech', parent: 'Tầng dữ liệu', icon: 'ph ph-ear', extra: true,
    name: 'Nhật ký lắng nghe', en: 'Mọi kênh đang nghe, hệ thống đã làm gì',
    title: 'Nhật ký lắng nghe', subtitle: 'Listening Log · tin và sự kiện của mọi kênh trên một dòng thời gian',
    description: 'Một dòng thời gian gộp mọi tin đang nghe và mọi thay đổi trên kênh (vào hoặc rời nhóm, đổi tên, kênh nối hoặc ngắt). Mỗi tin cho thấy hệ thống đã làm gì: sàng lọc thế nào, agent trực quyết định gì, bản nháp nằm ở đâu.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'rules', domain: 'tech', parent: 'Tầng dữ liệu', icon: 'ph ph-funnel',
    name: 'Quy tắc sàng lọc', en: 'Lọc, phân loại, chấm điểm',
    title: 'Quy tắc sàng lọc', subtitle: 'Refinery Rules · Sếp định nghĩa cách core agent đánh giá',
    description: 'Sếp định nghĩa cách core agent đọc dữ liệu thô: điều kiện nào thì gán nhãn gì, tính điểm theo trọng số nào, và khi nào mới đủ tin cậy để ghi vào kho sạch.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'clean', domain: 'tech', parent: 'Tầng dữ liệu', icon: 'ph ph-check-circle',
    name: 'Kho sạch', en: 'Dữ liệu đã lọc & bộ nhớ làm việc',
    title: 'Kho sạch & Trí nhớ', subtitle: 'Dữ liệu đã phân loại và trí nhớ tạm theo ID',
    description: 'Dữ liệu đã được core agent phân loại, gắn về ID nhóm và ID người. Agent trực kênh chỉ đọc từ đây cộng với trí nhớ tạm để quyết định nội dung phản hồi, rồi ghi kết quả trở lại.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'identity', domain: 'tech', parent: 'Tầng dữ liệu', icon: 'ph ph-git-merge',
    name: 'Hợp nhất danh tính', en: 'Gộp một người nhiều tài khoản',
    title: 'Hợp nhất danh tính', subtitle: 'Identity Resolution · một người, nhiều kênh',
    description: 'Cùng một người xuất hiện trên nhiều kênh phải gộp được, nếu không toàn bộ dữ liệu sẽ vỡ thành mảnh. Hệ thống chỉ gợi ý — quyết định gộp hoặc tách là của Sếp.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'agents', domain: 'tech', parent: 'Agent & Model', icon: 'ph ph-user-focus',
    name: 'Danh tính Agent', en: 'Tên, giọng, phạm vi trợ lý',
    title: 'Danh tính Agent', subtitle: 'Agent Identity · danh tính do Sếp định nghĩa',
    description: 'Agent là lớp mặt tiền có thể thay, không phải linh hồn cố định của hệ thống. Không có danh tính mặc định bắt buộc — Sếp tự đặt tên, vai trò, giọng nói, phạm vi kênh và mức tự trị cho từng agent.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'api', domain: 'tech', parent: 'Agent & Model', icon: 'ph ph-plugs',
    name: 'API & Model', en: 'Khoá API và model cho trợ lý',
    title: 'API & Model', subtitle: 'Khoá API, endpoint và tham số cho từng agent',
    description: 'Khoá API, endpoint và tham số sinh nội dung. Mỗi agent được gán một model riêng cho việc trực kênh, và core agent có model riêng cho việc sàng lọc dữ liệu thô.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'mcp', domain: 'tech', parent: 'Agent & Model', icon: 'ph ph-plugs-connected',
    name: 'MCP Hub', en: 'Máy chủ MCP bên ngoài',
    title: 'MCP Hub', subtitle: 'Máy chủ MCP bên ngoài mà agent được phép gọi',
    description: 'Cổng nối ra hệ thống bên ngoài: ERP, CRM, HRM, lịch, kho tài liệu. Agent chỉ gọi được những công cụ Sếp đã mở, và mọi lượt gọi đều ghi vào nhật ký.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'graph', domain: 'tech', parent: null, icon: 'ph ph-graph',
    name: 'Bản đồ quan hệ', en: 'Ai quen ai, qua đâu',
    title: 'Bản đồ quan hệ', subtitle: 'Relationship Map · ai đang liên quan',
    description: 'Không phải danh bạ. Trọng số của mỗi quan hệ thay đổi theo tần suất, chiều tương tác và giai đoạn — nên thấy được ai là cầu nối, khách nào đang lạnh, ai đang ôm quá nhiều việc.',
    descMaxWidth: 700, designTitleRow: true,
  },
  {
    key: 'notebook', domain: 'tech', parent: 'Bản đồ quan hệ', icon: 'ph ph-notebook',
    name: 'Sổ tay nhận thức', en: 'Ghi chú trợ lý theo từng người',
    title: 'Sổ tay nhận thức', subtitle: 'AI-trợ lý tự ghi nhận thức lũy tiến cho từng ID người và ID nhóm',
    description: 'Mỗi ID người và ID nhóm có một sổ tay riêng do AI-trợ lý tự ghi trong quá trình tương tác: nhận thức hiện tại, các mốc lũy tiến, và danh sách ID dữ liệu cần gọi lại khi cần. Đây là cửa sổ ngữ cảnh ngắn — không phải toàn bộ kho sạch.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'supply', domain: 'tech', parent: null, icon: 'ph ph-arrows-left-right',
    name: 'Cung ↔ Cầu', en: 'Ghép người cần với người có',
    title: 'Cung ↔ Cầu', subtitle: 'Ai cần mua, ai cần bán, ghép được với ai',
    description: 'Hai danh sách rút từ kho sạch: người đang cần nguồn hàng và người đang cần bán. Core agent đề xuất cặp ghép, Sếp quyết định có bắt tay hay không.',
    descMaxWidth: 760, designTitleRow: true,
  },
  {
    key: 'plugins', domain: 'tech', parent: null, icon: 'ph ph-puzzle-piece', navHidden: true,
    name: 'Plugin & Tiện ích', en: 'Plugin nền và plugin cài thêm',
    title: 'Plugin & Tiện ích', subtitle: 'Plugin nền DSH và plugin cài thêm từ bên ngoài',
    description: 'Mọi thành phần của hệ thống đều là plugin cắm rút nóng. Plugin nền DSH không tắt được vì khung gầm dựa vào chúng; plugin cài thêm thì tắt, cập nhật hoặc gỡ tuỳ ý. Một plugin lỗi chỉ ngắt mạch trong vùng cách ly của chính nó.',
    descMaxWidth: 760, designTitleRow: true,
  },
];

export const SCREEN_BY_KEY: Record<string, ScreenMeta> = Object.fromEntries(SCREENS.map((s) => [s.key, s]));

/** Level-1 order of the design NAV, per domain. Group entries are parent names. */
export const NAV_ORDER: Record<DomainId, string[]> = {
  business: ['overview', 'Hộp thư & Việc', 'Khách & Cơ hội', 'connections', 'team', 'system'],
  tech: ['Tầng dữ liệu', 'Agent & Model', 'graph', 'supply', 'plugins'],
};

export interface ScreenTreeGroup {
  /** Screen key when the group is itself a screen (Bản đồ quan hệ). */
  key: string | null;
  name: string;
  icon: string;
  children: ScreenMeta[];
}
export interface ScreenTreeDomain extends DomainMeta {
  entries: Array<{ kind: 'screen'; screen: ScreenMeta } | { kind: 'group'; group: ScreenTreeGroup }>;
}

/** The domain → group → screen tree the router and breadcrumbs are built from. */
export function buildScreenTree(): ScreenTreeDomain[] {
  return (Object.keys(NAV_ORDER) as DomainId[]).map((d) => ({
    ...DOMAINS[d],
    entries: NAV_ORDER[d].map((entry) => {
      const own = SCREEN_BY_KEY[entry];
      const groupName = own ? own.name : entry;
      const children = SCREENS.filter((s) => s.domain === d && s.parent === groupName);
      if (children.length) {
        return {
          kind: 'group' as const,
          group: {
            key: own ? own.key : null,
            name: own ? own.name : entry,
            icon: own ? own.icon : (GROUP_ICONS[entry] ?? 'ph ph-folder'),
            children,
          },
        };
      }
      if (!own) throw new Error(`NAV_ORDER entry "${entry}" is neither a screen nor a group`);
      return { kind: 'screen' as const, screen: own };
    }),
  }));
}
