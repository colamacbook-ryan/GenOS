import { useState } from 'react';
import { documentContentUrl, type DocSource, type DocumentDetail, type DocumentItem } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, FilterSelect, Icon, SelectField, TextField, type FilterOption } from '@gen-harness/ui';
import { errorText } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { DOC_SOURCE_LABEL, docIcon, docSourceTone, fmtBytes } from './relationsModel';
import { useCreateDocument, useDeleteDocument, useDirGroups, useDirPeople, useDocument, useDocuments } from './queries';

const SOURCE_OPTIONS: FilterOption<string>[] = [
  { value: '', label: 'Tất cả' },
  { value: 'channel', label: DOC_SOURCE_LABEL.channel },
  { value: 'agent', label: DOC_SOURCE_LABEL.agent },
  { value: 'tay', label: DOC_SOURCE_LABEL.tay },
];

export function DocumentsScreen() {
  const [source, setSource] = useUrlState<string>('src', '');
  const docs = useDocuments({ source: (source || undefined) as DocSource | undefined });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const del = useDeleteDocument();

  return (
    <div className="screen">
      <ScreenHead
        title="Tài liệu"
        description="Báo giá, hợp đồng, biên bản và tệp đã trao đổi trên các kênh, gắn về nhóm và người sở hữu. Ai được xem tài liệu nào do quyền truy cập của từng tài liệu quyết định."
        maxWidth={700}
        actions={
          <>
            <FilterSelect label="Nguồn" value={source} onChange={setSource} options={SOURCE_OPTIONS} />
            <Button variant="primary" icon="ph ph-upload-simple" onClick={() => setUploadOpen(true)}>
              Tải tài liệu lên
            </Button>
          </>
        }
      />

      <div className="table-card">
        {docs.isPending ? (
          <SkeletonLines rows={6} />
        ) : docs.isError ? (
          <CardError error={docs.error} onRetry={() => void docs.refetch()} retrying={docs.isFetching} />
        ) : docs.data.items.length === 0 ? (
          <EmptyState icon="ph ph-files" title="Chưa có tài liệu nào" description="Tải lên báo giá, hợp đồng hoặc tệp đã trao đổi để gắn về người hoặc nhóm." />
        ) : (
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Tài liệu">
              <thead>
                <tr>
                  <th>Tài liệu</th>
                  <th style={{ width: 90 }}>Kích thước</th>
                  <th style={{ width: 170 }}>Sở hữu</th>
                  <th style={{ width: 110 }}>Nguồn</th>
                  <th style={{ width: 150 }}>Cập nhật</th>
                  <th style={{ width: 140 }} />
                </tr>
              </thead>
              <tbody>
                {docs.data.items.map((d) => (
                  <DocRow key={d.id} d={d} onOpen={() => setOpenId(d.id)} onDelete={() => del.mutate(d.id)} deleting={del.isPending && del.variables === d.id} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {del.isError ? <InlineError>{errorText(del.error)}</InlineError> : null}
      </div>

      {uploadOpen ? <UploadDialog onClose={() => setUploadOpen(false)} /> : null}
      {openId ? <DocumentDialog id={openId} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function DocRow({ d, onOpen, onDelete, deleting }: { d: DocumentItem; onOpen: () => void; onDelete: () => void; deleting: boolean }) {
  return (
    <tr>
      <td>
        <div className="doc-cell">
          <Icon name={docIcon(d.mime)} size={16} color="var(--color-neutral-400)" />
          <div style={{ minWidth: 0 }}>
            <button type="button" className="doc-cell__title" onClick={onOpen}>
              {d.title}
            </button>
            {d.description ? <div className="doc-cell__desc">{d.description}</div> : null}
          </div>
        </div>
      </td>
      <td className="td-id">{fmtBytes(d.bytes)}</td>
      <td className="td-id">{d.owner?.name ?? '—'}</td>
      <td>
        <span className="mono-tag" style={{ color: docSourceTone(d.source) }}>{DOC_SOURCE_LABEL[d.source]}</span>
      </td>
      <td className="td-id">{fmtDMClock(d.updated_at)}</td>
      <td>
        <div className="doc-actions">
          <a className="gh-btn gh-btn--ghost btn-22" href={documentContentUrl(d.id)} target="_blank" rel="noreferrer">
            <Icon name="ph ph-download-simple" size={12} />
            Tải xuống
          </a>
          <Button variant="ghost" size="sm" icon="ph ph-trash" loading={deleting} onClick={onDelete} aria-label={`Xoá ${d.title}`} />
        </div>
      </td>
    </tr>
  );
}

function DocumentDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useDocument(id);
  return (
    <Dialog open onClose={onClose} width={480} title={q.data?.title ?? 'Tài liệu'} kicker={q.data ? `${DOC_SOURCE_LABEL[q.data.source]} · ${fmtBytes(q.data.bytes)}` : 'Đang tải…'}
      actions={
        <>
          {q.data ? (
            <a className="gh-btn gh-btn--secondary" href={documentContentUrl(q.data.id)} target="_blank" rel="noreferrer">
              <Icon name="ph ph-arrow-square-out" size={13} />
              Xem / tải xuống
            </a>
          ) : null}
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
        </>
      }
    >
      {q.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <DocumentAcl d={q.data} />
      )}
    </Dialog>
  );
}

function DocumentAcl({ d }: { d: DocumentDetail }) {
  return (
    <div className="dlg-fields">
      {d.description ? <p className="doc-detail-desc">{d.description}</p> : null}
      <div className="dlg-section-title">Quyền truy cập (ACL) — cộng thêm vào phạm vi mặc định của người/nhóm sở hữu</div>
      {d.acl.length === 0 ? (
        <span className="muted-note">Chưa có dòng ACL nào ngoài phạm vi mặc định.</span>
      ) : (
        <div className="doc-acl-list">
          {d.acl.map((row, i) => (
            <div className="doc-acl-row" key={i}>
              <span className="mono-tag">{row.principal}</span>
              <span className="doc-acl-row__perm" data-on={row.can_read}>
                <Icon name={row.can_read ? 'ph-fill ph-eye' : 'ph ph-eye-slash'} size={13} />
                Xem
              </span>
              <span className="doc-acl-row__perm" data-on={row.can_write}>
                <Icon name={row.can_write ? 'ph-fill ph-pencil-simple' : 'ph ph-pencil-simple-slash'} size={13} />
                Sửa
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? '');
      resolve(result.slice(result.indexOf(',') + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function UploadDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateDocument();
  const people = useDirPeople({});
  const groups = useDirGroups({});
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [ownerKind, setOwnerKind] = useState<'' | 'person' | 'group'>('');
  const [ownerId, setOwnerId] = useState('');

  const submit = async () => {
    if (!file || !title.trim()) return;
    const content_base64 = await fileToBase64(file);
    create.mutate(
      {
        title: title.trim(),
        description: description.trim() || null,
        filename: file.name,
        mime: file.type || 'application/octet-stream',
        content_base64,
        owner_person_id: ownerKind === 'person' ? ownerId || undefined : undefined,
        owner_group_id: ownerKind === 'group' ? ownerId || undefined : undefined,
      },
      { onSuccess: onClose },
    );
  };

  const ownerOptions =
    ownerKind === 'person'
      ? (people.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))
      : ownerKind === 'group'
        ? (groups.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }))
        : [];

  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Tải tài liệu lên"
      kicker="Tối đa 20MB/tệp"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-upload-simple" loading={create.isPending} disabled={!file || !title.trim()} onClick={() => void submit()}>
            Tải lên
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <TextField label="Tiêu đề" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        <TextField label="Mô tả (không bắt buộc)" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={400} />
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="doc-file">Tệp</label>
          <input
            id="doc-file"
            type="file"
            className="gh-input"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <SelectField
          label="Sở hữu"
          value={ownerKind}
          onChange={(e) => {
            setOwnerKind(e.target.value as '' | 'person' | 'group');
            setOwnerId('');
          }}
          options={[{ value: '', label: 'Không gắn — chỉ Owner thấy' }, { value: 'person', label: 'Một người' }, { value: 'group', label: 'Một nhóm' }]}
        />
        {ownerKind ? (
          <SelectField
            label={ownerKind === 'person' ? 'Người sở hữu' : 'Nhóm sở hữu'}
            value={ownerId}
            onChange={(e) => setOwnerId(e.target.value)}
            options={[{ value: '', label: 'Chọn…' }, ...ownerOptions]}
          />
        ) : null}
        {create.isError ? <InlineError>{errorText(create.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
