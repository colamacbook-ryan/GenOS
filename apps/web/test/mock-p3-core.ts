/**
 * Mock API giai đoạn 3 · nền chung (chứng cứ, góc nhìn, bản nháp). `handle` trả true khi đã trả lời request.
 * Dữ liệu mẫu lấy từ docs/design/seed-data.json để màn hiện đúng như thiết kế.
 *
 * Cụm khác cần chứng cứ riêng cho đối tượng của mình: `registerExplain(kind, fn)` (fn trả `Explain` hoặc null).
 */
import type { DraftDetail, DraftItem, Explain, ExplainUnit, SavedView } from '@gen-harness/contracts';
import seed from '../../../docs/design/seed-data.json';
import { USER_IDS, agentIdByName } from './mock-ids';
import type { P2Ctx } from './mock-phase2';

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

type ExplainFn = (id: string) => Explain | null;
const explainRegistry = new Map<string, ExplainFn>();
export function registerExplain(kind: string, fn: ExplainFn): void {
  explainRegistry.set(kind, fn);
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const AGE_MIN: Record<string, number> = {
  '47 phút': 47,
  '12 phút': 12,
  '18 phút': 18,
  '2 giờ': 120,
  '3 giờ': 180,
  '5 giờ': 300,
};
const KIND: Record<string, DraftItem['kind']> = {
  'Báo giá': 'quotation',
  'Tin nhắn': 'message',
  'Hợp đồng': 'contract',
  'Nhắc việc': 'reminder',
  'Báo cáo': 'report',
};

export const BAO = {
  id: 'p-bao',
  code: 'PER-0042',
  name: 'Nguyễn Văn Bảo',
  type: 'customer' as const,
  org_name: 'Công ty in Thành Phát',
};
export const GROUP_TP = {
  id: 'g-thanhphat',
  code: 'GRP-ZL-0114',
  name: 'Đối tác in ấn Thành Phát',
  channel: 'zalo' as const,
};

const COMPLAINT = seed.meaningItems[1];
export function sampleUnit(i = 0): ExplainUnit {
  const m = seed.meaningItems[i] ?? COMPLAINT;
  return {
    id: `mu-${i}`,
    event_type: i === 1 ? 'Complained' : 'AskedPrice',
    conclusion: m.title,
    confidence: 0.93,
    observed_at: ago(18 + i * 30),
    group: {
      id: `g-${i}`,
      code: `GRP-ZL-01${10 + i}`,
      name: m.source.split(' · ')[0],
      channel: 'zalo',
    },
    person: BAO,
    quotes: [
      {
        raw_id: `raw-${i}`,
        raw_code: `RAW-91842${i}`,
        quote: m.evidence.replace(/^"|"$/g, ''),
        occurred_at: ago(18 + i * 30),
        channel: 'zalo',
        sender: BAO,
      },
    ],
  };
}

function defaultExplain(kind: string, id: string): Explain {
  const u = sampleUnit(1);
  return {
    kind,
    id,
    title: kind === 'score' ? 'Nguyễn Văn Bảo — độ nóng' : 'Vì sao hệ thống nghĩ vậy',
    statement: '87/100 · tin cậy 0,91',
    method: 'rules+model',
    factors: [
      {
        label: `Complained: ${u.conclusion}`,
        value: 87,
        evidence: [{ type: 'meaning_unit', id: u.id }],
      },
    ],
    units: [u],
    history:
      kind === 'score'
        ? [
            {
              value: 87,
              computed_at: ago(20),
              method: 'rules+model',
              by: null,
            },
            {
              value: 72,
              computed_at: ago(60 * 26),
              method: 'rules+model',
              by: null,
            },
          ]
        : [],
  };
}

function seedDrafts(): DraftDetail[] {
  return seed.drafts.map((d, i) => {
    const kind = KIND[d.kind] ?? 'message';
    const sendable = kind === 'message' || kind === 'quotation' || kind === 'contract';
    const body = i === 0 ? seed.draftBody : [d.title];
    return {
      id: `draft-${d.id}`,
      code: d.id,
      kind,
      kind_label: d.kind,
      title: d.title,
      agent: { id: agentIdByName(d.agent), name: d.agent },
      created_by: null,
      created_at: ago(AGE_MIN[d.age] ?? 30),
      status: 'pending',
      hold_reason: i === 0 ? 'vượt ngưỡng 50.000.000 ₫' : sendable ? 'ghi ra ngoài' : null,
      subject: i < 2 ? BAO : null,
      paragraphs: body,
      text: body.join('\n\n'),
      lang: 'vi',
      target: sendable
        ? {
            channel: 'zalo',
            thread_type: 'group',
            group: GROUP_TP,
            person: null,
          }
        : null,
      amount_vnd: i === 0 ? 84_000_000 : null,
      autonomy_level: 4,
      flags: {
        writes_external: sendable,
        personnel_related: false,
        over_threshold: i === 0,
      },
      approve_label: sendable ? 'Duyệt và gửi qua Zalo' : 'Duyệt và thực hiện',
      sources:
        i === 0
          ? seed.draftSources.map((s, k) => ({
              label: s.label,
              ref: k === 0 ? { type: 'meaning_unit', id: 'mu-1' } : null,
            }))
          : [],
      context:
        i === 0
          ? seed.wbContext.map((c) => ({
              key: c.key,
              value: c.value,
              ref: c.key === 'độ nóng' ? { type: 'score', id: `person:${BAO.id}:heat` } : null,
            }))
          : [],
      side_actions:
        i === 0
          ? seed.wbSideActions.map((s, k) => ({
              key: `side-${k}`,
              label: s.label,
              on: s.on,
            }))
          : [],
      decision: null,
      send_result: null,
      versions: [],
    } satisfies DraftDetail;
  });
}

const LIST_KEYS = [
  'id',
  'code',
  'kind',
  'kind_label',
  'title',
  'agent',
  'created_by',
  'created_at',
  'status',
  'hold_reason',
  'subject',
  'resent_by',
] as const;
const listItem = (d: DraftDetail): DraftItem => Object.fromEntries(LIST_KEYS.map((k) => [k, d[k]])) as unknown as DraftItem;

export function createMock(opts: P3Options) {
  let drafts: DraftDetail[] = opts.fresh ? [] : seedDrafts();
  let views: SavedView[] = [];
  const timers: ReturnType<typeof setTimeout>[] = [];
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';

  function decide(ctx: P2Ctx, d: DraftDetail, verdict: 'approve' | 'edit' | 'reject'): true {
    if (d.status !== 'pending') return ctx.problem(409, 'DRAFT_DECIDED', 'Bản nháp này đã được quyết định');
    const body = ctx.body as { text?: string; reason?: string | null };
    if (verdict === 'edit') {
      if (!body.text?.trim()) return ctx.problem(422, 'VALIDATION', 'Nội dung gửi không được để trống');
      d.versions = [
        ...d.versions,
        {
          at: new Date().toISOString(),
          by: d.agent ? 'agent' : 'user',
          text: d.text,
        },
      ];
      d.text = body.text;
      d.paragraphs = body.text.split(/\n\s*\n/).filter(Boolean);
    }
    d.decision = {
      by: { id: USER_IDS.owner, name: ctx.userLabel },
      at: new Date().toISOString(),
      reason: body.reason ?? null,
    };
    if (verdict === 'reject') {
      d.status = 'rejected';
      // Huỷ bản gửi lại ⇒ bản lỗi cũ về lại thẻ "Gửi lỗi".
      for (const x of drafts) if (x.resent_by?.id === d.id) x.resent_by = null;
    } else if (d.target) {
      d.status = verdict === 'edit' ? 'edited' : 'approved';
      timers.push(
        setTimeout(() => {
          d.status = 'sent';
          d.send_result = {
            ok: true,
            error: null,
            at: new Date().toISOString(),
            external_msg_id: `zmsg-${d.code}`,
          };
          opts.emit('draft.updated', {
            id: d.id,
            status: d.status,
            send_result: d.send_result,
          });
        }, 600),
      );
    } else d.status = 'sent';
    opts.emit('draft.updated', {
      id: d.id,
      status: d.status,
      send_result: d.send_result,
    });
    return ctx.reply(200, d);
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);

    if (seg[0] === 'explain') {
      if (seg[1] === 'raw' && seg[2] && m === 'GET') {
        const u = [0, 1, 2, 3, 4].map(sampleUnit).find((x) => x.quotes[0].raw_id === seg[2]);
        if (!u) return problem(404, 'NOT_FOUND', 'Bản ghi không tồn tại hoặc ngoài phạm vi của bạn');
        const q = u.quotes[0];
        return reply(200, {
          id: q.raw_id,
          code: q.raw_code,
          received_at: q.occurred_at,
          occurred_at: q.occurred_at,
          channel: { type: 'zalo', label: 'Zalo' },
          group: u.group,
          person: u.person,
          text: q.quote,
          kind: 'text',
          label: u.event_type,
          confidence: u.confidence,
          state: 'clean',
          payload: { text: q.quote },
          meaning_units: [],
        });
      }
      if (seg.length === 3 && m === 'GET') {
        const [kind, id] = [seg[1], decodeURIComponent(seg[2])];
        const custom = explainRegistry.get(kind)?.(id);
        return reply(200, custom ?? defaultExplain(kind, id));
      }
    }

    if (seg[0] === 'views') {
      if (seg.length === 1 && m === 'GET') {
        const screen = url.searchParams.get('screen');
        return reply(
          200,
          views.filter((v) => !screen || v.screen === screen),
        );
      }
      if (seg.length === 1 && m === 'POST') {
        const b = body as {
          screen: string;
          name: string;
          filters?: Record<string, string>;
        };
        if (views.some((v) => v.screen === b.screen && v.name.toLowerCase() === b.name.trim().toLowerCase()))
          return problem(409, 'VIEW_EXISTS', 'Đã có góc nhìn cùng tên trên màn này');
        const v: SavedView = {
          id: `view-${views.length + 1}-${Date.now()}`,
          screen: b.screen,
          name: b.name.trim(),
          filters: b.filters ?? {},
          created_at: new Date().toISOString(),
        };
        views = [...views, v];
        return reply(201, v);
      }
      if (seg.length === 2 && m === 'DELETE') {
        if (!views.some((v) => v.id === seg[1])) return problem(404, 'NOT_FOUND', 'Góc nhìn không tồn tại');
        views = views.filter((v) => v.id !== seg[1]);
        return reply(204);
      }
    }

    if (seg[0] === 'drafts') {
      if (!has(ctx, 'action.draft') && !has(ctx, 'action.approve')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const status = url.searchParams.get('status') ?? 'pending';
        const kind = url.searchParams.get('kind');
        const rows = drafts
          .filter((d) =>
            status === 'pending'
              ? d.status === 'pending'
              : status === 'decided'
                ? d.status !== 'pending'
                : status === 'failed'
                  ? d.status === 'failed' && !d.resent_by // thẻ "Gửi lỗi": bỏ bản đã có bản gửi lại
                  : true,
          )
          .filter((d) => !kind || d.kind === kind);
        return reply(200, {
          items: rows.map(listItem),
          next_cursor: null,
          total: rows.length,
        });
      }
      if (seg.length === 1 && m === 'POST') {
        const b = body as {
          kind: DraftItem['kind'];
          title: string;
          text: string;
          resend_of?: string | null;
        };
        const old = b.resend_of ? drafts.find((x) => x.id === b.resend_of) : undefined;
        if (b.resend_of && !old) return problem(404, 'NOT_FOUND', 'Bản nháp không tồn tại hoặc ngoài phạm vi của bạn');
        if (old && (old.status !== 'failed' || old.kind !== b.kind))
          return problem(409, 'NOT_RESENDABLE', 'Chỉ gửi lại được bản nháp gửi lỗi, cùng loại với bản mới');
        if (old?.resent_by) return problem(409, 'ALREADY_RESENT', `Bản này đã được gửi lại (${old.resent_by.code}) — mở bản mới để duyệt`);
        const d: DraftDetail = {
          ...seedDrafts()[1],
          id: `draft-new-${Date.now()}`,
          code: `ACT-0${240 + drafts.length}`,
          kind: b.kind,
          title: b.title,
          text: b.text,
          paragraphs: b.text.split(/\n\s*\n/),
          agent: null,
          created_by: { id: USER_IDS.owner, name: ctx.userLabel },
          created_at: new Date().toISOString(),
        };
        drafts = [d, ...drafts];
        if (old) old.resent_by = { id: d.id, code: d.code };
        opts.emit('draft.new', listItem(d));
        return reply(201, d);
      }
      const d = drafts.find((x) => x.id === seg[1]);
      if (!d) return problem(404, 'NOT_FOUND', 'Bản nháp không tồn tại hoặc ngoài phạm vi của bạn');
      if (seg.length === 2 && m === 'GET') return reply(200, d);
      if (seg.length === 3 && m === 'POST') {
        const action = seg[2];
        if (action === 'translate')
          return reply(200, {
            lang: (body as { lang: string }).lang,
            text: `[${(body as { lang: string }).lang}] ${d.text}`,
          });
        if (action === 'regenerate') {
          d.versions = [...d.versions, { at: new Date().toISOString(), by: 'agent', text: d.text }];
          return reply(200, d);
        }
        if (!has(ctx, 'action.approve')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (ctx.needPin())
          return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', {
            detail: { operation: 'draft.decide' },
          });
        if (action === 'approve') return decide(ctx, d, 'approve');
        if (action === 'edit-send') return decide(ctx, d, 'edit');
        if (action === 'reject') return decide(ctx, d, 'reject');
      }
    }

    if (p === '/agents/decisions' && m === 'GET') return reply(200, { items: [], next_cursor: null, total: 0 });
    return false;
  }

  return {
    handle,
    hooks: {
      /** Test: đặt lại danh sách bản nháp. */
      setDrafts: (rows: DraftDetail[]) => {
        drafts = rows;
      },
      drafts: () => drafts,
      /** Cụm khác (vd market "Giới thiệu hai bên") tạo bản nháp qua đây — cùng cơ chế `create_draft` dùng chung ở backend. */
      push: (d: DraftDetail) => {
        drafts = [d, ...drafts];
        opts.emit('draft.new', listItem(d));
        return d;
      },
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => timers.forEach(clearTimeout),
  };
}
