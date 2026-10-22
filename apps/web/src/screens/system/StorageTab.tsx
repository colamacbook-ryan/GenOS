import { useMemo, useState } from 'react';
import { ApiError, type RetentionEditableDataset, type RetentionPolicy } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, SelectField, TextField } from '@gen-harness/ui';
import { useDirPeople } from '../relations/queries';
import { downloadBlob } from '../../lib/download';
import { fmtDMClock, fmtInt } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { errorText } from '../../lib/errorText';
import { toast } from '../../lib/toast';
import { useDebouncedValue } from '../../lib/useDebouncedValue';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { UpdateCard } from '../../update/UpdateCard';
import { BackupPanel } from './BackupPanel';
import { OffsitePanel } from './OffsitePanel';
import { HealthCard } from './HealthCard';
import { RemoteAccessCard } from './RemoteAccessCard';
import { DATA_REQUEST_KIND, RETENTION_LABEL, personExportFileName, RETENTION_OWNER_ONLY_REASON, retentionConfirmText, retentionDeletes, retentionOwnerOnly, retentionRowView } from './systemModel';
import { useCreateDataRequest, usePatchRetention, usePersonDataRequests, useRetentionPolicies } from './queries';

/** Cài đặt › Sao lưu & cập nhật (trước v0.1.42: Dữ liệu & lưu trữ) — sao lưu & khôi phục (v0.1.20), bản sao ngoài máy (v0.1.40); spec I: hạn lưu theo tập dữ liệu, yêu cầu xuất/xoá/giới hạn
 * dữ liệu một người (PLAN 4.5). */
export function StorageTab() {
  const canRead = useCan('system.read');
  const canManage = useCan('system.manage');
  if (!canRead) {
    return (
      <div className="gh-card">
        <EmptyState icon="ph ph-lock-simple" title="Vai trò của bạn không xem được Sao lưu & cập nhật" />
      </div>
    );
  }
  return (
    <div className="sys-tabs-col">
      {/* v0.1.36 (F-6): sức khoẻ hệ thống đứng đầu — Bộ xử lý nền, sao lưu, cập nhật, ổ đĩa. */}
      <HealthCard />
      {/* v0.1.46 (F-21): Truy cập từ xa — địa chỉ đăng nhập cho nhân viên, cảnh báo cổng mở cho cả mạng. */}
      <RemoteAccessCard />
      {/* v0.1.30: mục cập nhật cố định — thẻ Tổng quan chỉ hiện khi đã biết có bản mới. */}
      {canManage ? <UpdateCard always /> : null}
      <BackupPanel />
      {/* v0.1.40 (F-12): bản sao ra ổ USB/NAS — ngay sau Sao lưu & khôi phục. */}
      <OffsitePanel />
      <RetentionPanel />
      <PersonDataRequestPanel />
    </div>
  );
}

/**
 * v0.1.40 (F-2): việc nền dọn dữ liệu theo hạn lưu đã chạy thật — nút "Sửa" mở lại cho dòng `editable`. Dòng
 * `not_applicable` (Nhật ký hành động — chỉ ghi thêm) hiện "Không áp dụng"; `agent.browser_jobs.result` cố định
 * 14 ngày. "Ẩn danh sau" không hiện (hệ thống chưa thi hành ẩn danh — không hứa điều chưa làm); PATCH gửi lại nguyên
 * `anonymize_after_days` đang có để không xoá cấu hình cũ.
 *
 * Lưu số ngày cho tập dữ liệu bị xoá thật ⇒ hỏi lại ngay dưới dòng ("… sẽ bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp") rồi mới
 * gửi `confirm_delete: true`. Hạn đặt trước v0.1.40 (`needs_confirm`) hiện "chưa áp dụng". Bảng phân vùng (xoá cả tháng
 * cho mọi tổ chức) chỉ Owner sửa được.
 */
function RetentionPanel() {
  const canManage = useCan('system.manage');
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const tz = useOrgTimezone();
  const q = useRetentionPolicies();
  const patch = usePatchRetention();
  const [editing, setEditing] = useState<RetentionEditableDataset | null>(null);
  const [keepDays, setKeepDays] = useState('');
  const [confirming, setConfirming] = useState<number | null>(null);

  const startEdit = (r: RetentionPolicy) => {
    patch.reset();
    setConfirming(null);
    setEditing(r.dataset as RetentionEditableDataset);
    setKeepDays(r.keep_days != null ? String(r.keep_days) : '');
  };
  const cancel = () => {
    setConfirming(null);
    setEditing(null);
  };
  const send = (r: RetentionPolicy, keep: number | null, confirm: boolean) => {
    patch.mutate(
      {
        dataset: r.dataset as RetentionEditableDataset,
        keep_days: keep,
        anonymize_after_days: r.anonymize_after_days,
        ...(confirm ? { confirm_delete: true } : {}),
      },
      { onSuccess: cancel },
    );
  };
  const save = (r: RetentionPolicy) => {
    const keep = keepDays.trim() ? Number(keepDays) : null;
    // Số ngày hợp lệ cho tập bị xoá thật ⇒ hỏi lại trước; số sai (0, chữ…) gửi luôn để API báo lỗi dưới ô.
    if (keep != null && Number.isInteger(keep) && keep >= 1 && retentionDeletes(r)) {
      patch.reset();
      setConfirming(keep);
      return;
    }
    send(r, keep, false);
  };
  const fieldErrors = patch.error instanceof ApiError && patch.error.status === 422 ? patch.error.fieldErrors : {};
  const keepError = typeof fieldErrors.keep_days === 'string' ? fieldErrors.keep_days : null;
  const otherFieldErrors = Object.entries(fieldErrors).filter(([k, m]) => k !== 'keep_days' && typeof m === 'string');

  return (
    <Panel
      genTarget="system.storage.retention"
      title="Hạn lưu dữ liệu"
      kicker="Mỗi tập dữ liệu một hạn — đổi cần mã PIN"
      label="Hạn lưu dữ liệu"
      bodyClass="retention-wrap"
    >
      {q.isPending ? (
        <SkeletonLines rows={5} padding="10px 16px" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <table className="retention-table">
          <thead>
            <tr>
              <th>Tập dữ liệu</th>
              <th>Giữ trong</th>
              {canManage ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {q.data.flatMap((r) => {
              const view = retentionRowView(r, tz, { isOwner, canManage });
              const label = RETENTION_LABEL[r.dataset] ?? r.dataset;
              const ownerLocked = retentionOwnerOnly(r) && !isOwner;
              const isEditing = editing === r.dataset;
              const rows = [
                <tr key={r.dataset} data-testid={`retention-${r.dataset}`}>
                  <td>
                    <div className="retention-table__name">{label}</div>
                    <details className="retention-table__tech">
                      <summary>Chi tiết kỹ thuật</summary>
                      <div className="mono retention-table__code">{r.dataset}</div>
                    </details>
                    {view.note ? <div className="retention-table__note">{view.note}</div> : null}
                    {view.pending ? <div className="retention-table__pending">{view.pending}</div> : null}
                    {view.lockedReason ? <div className="retention-table__note" data-testid={`retention-locked-${r.dataset}`}>{view.lockedReason}</div> : null}
                    {view.lastRun ? (
                      <div className={view.lastFailed ? 'retention-table__pending' : 'retention-table__last'} data-testid={view.lastFailed ? `retention-failed-${r.dataset}` : undefined}>
                        {view.lastRun}
                      </div>
                    ) : null}
                  </td>
                  {isEditing ? (
                    <>
                      <td>
                        <TextField
                          label={`Giữ trong (ngày) — ${r.dataset}`}
                          type="number"
                          min={1}
                          max={3650}
                          value={keepDays}
                          onChange={(e) => {
                            setConfirming(null);
                            setKeepDays(e.target.value);
                          }}
                          placeholder="mãi mãi"
                          error={keepError}
                        />
                      </td>
                      <td className="retention-table__actions">
                        <Button variant="ghost" className="btn-27" onClick={cancel}>
                          Huỷ
                        </Button>
                        <Button variant="primary" className="btn-27" loading={patch.isPending && confirming == null} disabled={confirming != null} onClick={() => save(r)}>
                          Lưu
                        </Button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className={view.applicable ? 'mono' : undefined}>{view.keep}</td>
                      {canManage ? (
                        <td className="retention-table__actions">
                          {view.editable ? (
                            <Button
                              variant="ghost"
                              className="btn-27"
                              icon="ph ph-pencil-simple"
                              disabled={ownerLocked}
                              title={ownerLocked ? RETENTION_OWNER_ONLY_REASON : undefined}
                              onClick={() => startEdit(r)}
                              aria-label={`Sửa hạn lưu ${label}`}
                            >
                              Sửa
                            </Button>
                          ) : null}
                        </td>
                      ) : null}
                    </>
                  )}
                </tr>,
              ];
              if (isEditing && confirming != null) {
                rows.push(
                  <tr key={`${r.dataset}-confirm`} className="retention-table__confirm-row">
                    <td colSpan={canManage ? 3 : 2}>
                      <div className="retention-confirm" role="alert" data-testid="retention-confirm">
                        <Icon name="ph ph-warning" size={14} />
                        <span>{retentionConfirmText(r, confirming)}</span>
                        <div className="retention-confirm__actions">
                          <Button variant="ghost" className="btn-27" onClick={() => setConfirming(null)}>
                            Quay lại
                          </Button>
                          <Button variant="primary" className="btn-27" icon="ph ph-trash" loading={patch.isPending} onClick={() => send(r, confirming, true)}>
                            Đồng ý xoá dữ liệu quá hạn
                          </Button>
                        </div>
                      </div>
                    </td>
                  </tr>,
                );
              }
              return rows;
            })}
          </tbody>
        </table>
      )}
      {patch.isError && !keepError ? (
        <InlineError>
          {otherFieldErrors.length ? otherFieldErrors.map(([, m]) => m).join(' · ') : errorText(patch.error)}
        </InlineError>
      ) : null}
    </Panel>
  );
}

/** Người được chọn để xuất / ẩn danh hoá — giữ riêng để vẫn còn khi ô tìm kiếm đổi và danh sách kết quả thay đổi. */
interface PickedPerson {
  id: string;
  name: string;
  code: string;
}

/**
 * Yêu cầu xuất / ẩn danh hoá / giới hạn dữ liệu một người (spec I).
 *
 * - Chọn người bằng tìm kiếm Ở MÁY CHỦ (tên hoặc mã) — không chỉ 50 người đầu.
 * - "Xuất dữ liệu": máy chủ trả gói dữ liệu ngay trong phản hồi (không lưu lại) ⇒ giao diện TẢI NGAY thành tệp JSON; chỉ
 *   báo "đã tải" khi thật sự có tệp.
 * - "Ẩn danh hoá" (trước đây "Xoá dữ liệu"): hộp xác nhận nêu rõ xoá gì và KHÔNG xoá tin nhắn gốc.
 */
function PersonDataRequestPanel() {
  const canManage = useCan('system.manage');
  const tz = useOrgTimezone();
  const [search, setSearch] = useState('');
  const q = useDebouncedValue(search.trim(), 300);
  const people = useDirPeople({ q: q || undefined, limit: 30 });
  const [picked, setPicked] = useState<PickedPerson | null>(null);
  const [confirmErase, setConfirmErase] = useState(false);
  const personId = picked?.id ?? '';
  const create = useCreateDataRequest();
  const history = usePersonDataRequests(personId, !!personId);

  const options = useMemo(() => {
    const list: PickedPerson[] = (people.data?.items ?? []).map((p) => ({ id: p.id, name: p.name, code: p.code }));
    // Người đang chọn luôn có mặt trong danh sách, kể cả khi kết quả tìm kiếm mới không còn nêu họ.
    return picked && !list.some((p) => p.id === picked.id) ? [picked, ...list] : list;
  }, [people.data, picked]);
  const matched = people.data?.total ?? 0;
  const shown = people.data?.items.length ?? 0;

  const request = (kind: 'export' | 'erase' | 'restrict') => {
    if (!picked) return;
    create.mutate(
      { personId: picked.id, kind },
      {
        onSuccess: (res) => {
          if (kind === 'export') {
            // Chỉ nói "đã tải" khi thật sự có tệp: máy chủ không lưu gói, nên không có dữ liệu = không có gì để đưa.
            if (res.result == null) {
              toast('Máy chủ không trả dữ liệu để xuất — thử lại, nếu vẫn vậy hãy báo quản trị', 'bad');
              return;
            }
            try {
              downloadBlob(new Blob([JSON.stringify(res.result, null, 2)], { type: 'application/json' }), personExportFileName(picked.code));
              toast(`Đã tải tệp dữ liệu của ${picked.name}`, 'ok');
            } catch {
              toast('Đã lấy được dữ liệu nhưng trình duyệt không tạo được tệp tải về — thử lại', 'bad');
            }
          } else if (kind === 'erase') {
            toast('Đã ẩn danh hoá người này — tin nhắn gốc vẫn được giữ', 'ok');
          } else {
            toast('Đã giới hạn dùng dữ liệu người này', 'ok');
          }
        },
        onError: (e) => toast(errorText(e), 'bad'),
      },
    );
  };

  return (
    <Panel
      title="Yêu cầu xuất / xoá dữ liệu một người"
      kicker="Có ghi vào Nhật ký — tin nhắn gốc không bị xoá"
      label="Yêu cầu xuất / xoá dữ liệu một người"
      bodyClass="data-request"
    >
      <div className="data-request__picker">
        <TextField
          label="Tìm người (tên hoặc mã)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Nguyễn Văn Bảo, PER-0042…"
          hint={people.data && matched > shown ? `Hiện ${fmtInt(shown)} / ${fmtInt(matched)} người khớp — gõ thêm để thu hẹp` : undefined}
        />
        <SelectField
          label="Người"
          value={personId}
          onChange={(e) => setPicked(options.find((p) => p.id === e.target.value) ?? null)}
          options={[{ value: '', label: people.isPending ? 'Đang tải…' : 'Chọn người' }, ...options.map((p) => ({ value: p.id, label: `${p.name} · ${p.code}` }))]}
        />
      </div>
      {people.isError ? <CardError error={people.error} onRetry={() => void people.refetch()} retrying={people.isFetching} /> : null}

      <div className="data-request__actions">
        {(['export', 'erase', 'restrict'] as const).map((kind) => {
          const info = DATA_REQUEST_KIND[kind];
          return (
            <Button
              key={kind}
              variant="secondary"
              icon={info.icon}
              disabled={!personId || create.isPending || !canManage}
              loading={create.isPending && create.variables?.kind === kind}
              onClick={() => (kind === 'erase' ? setConfirmErase(true) : request(kind))}
            >
              {info.label}
            </Button>
          );
        })}
      </div>
      {!canManage ? <p className="muted-note">Cần quyền quản lý hệ thống để gửi yêu cầu.</p> : null}
      {create.isError ? <InlineError>{errorText(create.error)}</InlineError> : null}

      {personId ? (
        <div className="data-request__history">
          <div className="data-request__history-title">Lịch sử yêu cầu — {picked?.name ?? '…'}</div>
          {history.isPending ? (
            <SkeletonLines rows={2} padding="0" />
          ) : history.isError ? (
            <CardError error={history.error} onRetry={() => void history.refetch()} retrying={history.isFetching} />
          ) : history.data.length === 0 ? (
            <p className="muted-note">Chưa có yêu cầu nào cho người này.</p>
          ) : (
            history.data.map((r) => (
              <div className="data-request__row" key={r.id}>
                <Icon name={DATA_REQUEST_KIND[r.kind]?.icon ?? 'ph ph-file'} size={14} color={DATA_REQUEST_KIND[r.kind]?.tone} />
                <span style={{ flex: 1, minWidth: 0 }}>{DATA_REQUEST_KIND[r.kind]?.label ?? r.kind}</span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>
                  {fmtDMClock(r.requested_at, tz)}
                </span>
              </div>
            ))
          )}
        </div>
      ) : null}
      {confirmErase && picked ? (
        <Dialog
          open
          onClose={() => setConfirmErase(false)}
          width={440}
          title="Ẩn danh hoá người này?"
          kicker={`${picked.name} · ${picked.code}`}
          actions={
            <>
              <Button variant="secondary" onClick={() => setConfirmErase(false)}>
                Huỷ
              </Button>
              <Button
                variant="primary"
                icon="ph ph-eye-slash"
                onClick={() => {
                  setConfirmErase(false);
                  request('erase');
                }}
              >
                Ẩn danh hoá
              </Button>
            </>
          }
        >
          <div className="dlg-fields" data-testid="erase-confirm">
            <p className="muted-note">
              Sẽ xoá điểm số, sổ tay nhận thức, handle và số điện thoại gắn với người này; tên đổi thành “Người dùng đã xoá”. Việc này không hoàn tác được.
            </p>
            <p className="muted-note">
              <strong>Không xoá tin nhắn gốc</strong> — tin thô vẫn còn trong kho, chỉ không còn nhận ra là của người này.
            </p>
          </div>
        </Dialog>
      ) : null}
    </Panel>
  );
}
