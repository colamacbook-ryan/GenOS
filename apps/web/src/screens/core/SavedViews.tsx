import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Dialog, EmptyState, IconButton, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { useActiveScreenKey } from '../../shell/routeHandles';
import { CardError, InlineError, SkeletonLines } from '../common';
import { qk3, useViews } from './queries';
import { searchToFilters, viewHref } from './viewsModel';

/** Nút "Góc nhìn đã lưu" ở header: mở, lưu, xoá góc nhìn của màn đang xem (GET/POST/DELETE /views). */
export function SavedViewsButton() {
  const screen = useActiveScreenKey();
  const [open, setOpen] = useState(false);
  return (
    <>
      <IconButton
        icon="ph ph-bookmark-simple"
        label="Góc nhìn đã lưu"
        variant="secondary"
        className="hd-saved"
        onClick={() => setOpen(true)}
        disabled={!screen}
      />
      {open && screen ? <SavedViewsDialog screen={screen} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function SavedViewsDialog({ screen, onClose }: { screen: string; onClose: () => void }) {
  const { search } = useLocation();
  const navigate = useNavigate();
  const views = useViews(screen);
  const [name, setName] = useState('');
  const filters = searchToFilters(search);

  const save = useMutation({
    mutationFn: () => api.views.create({ screen, name: name.trim(), filters }),
    onSuccess: (v) => {
      void queryClient.invalidateQueries({ queryKey: ['views'] });
      setName('');
      toast(`Đã lưu góc nhìn "${v.name}"`);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.views.remove(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: qk3.views(screen) }),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Góc nhìn đã lưu"
      kicker="Bộ lọc, tab và mục đang mở của màn này"
      actions={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      <div className="dlg-fields">
        {views.isPending ? (
          <SkeletonLines rows={2} padding="0" />
        ) : views.isError ? (
          <CardError error={views.error} onRetry={() => void views.refetch()} retrying={views.isFetching} />
        ) : views.data.length === 0 ? (
          <EmptyState
            icon="ph ph-bookmark-simple"
            title="Chưa có góc nhìn nào"
            description="Lưu bộ lọc đang dùng để mở lại bằng một lần bấm."
          />
        ) : (
          <div className="dlg-list" role="list">
            {views.data.map((v) => (
              <div key={v.id} className="dlg-row sv-row" role="listitem">
                <button
                  type="button"
                  className="sv-open"
                  onClick={() => {
                    navigate(viewHref(v.screen, v.filters));
                    onClose();
                  }}
                >
                  <span className="sv-open__name">{v.name}</span>
                  <span className="sv-open__meta">{Object.keys(v.filters).length} bộ lọc</span>
                </button>
                <IconButton icon="ph ph-trash" label={`Xoá góc nhìn ${v.name}`} variant="ghost" onClick={() => remove.mutate(v.id)} />
              </div>
            ))}
          </div>
        )}
        <form
          className="sv-save"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) save.mutate();
          }}
        >
          <TextField
            label="Lưu góc nhìn hiện tại"
            placeholder="Tên góc nhìn"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" variant="primary" icon="ph ph-bookmark-simple" disabled={!name.trim()} loading={save.isPending}>
            Lưu góc nhìn
          </Button>
        </form>
        {save.isError ? <InlineError>{errorText(save.error)}</InlineError> : null}
        {remove.isError ? <InlineError>{errorText(remove.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
