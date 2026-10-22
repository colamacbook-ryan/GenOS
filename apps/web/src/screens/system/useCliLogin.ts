import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, type CliKind, type CliLoginEvent } from '@gen-harness/contracts';
import { BOSS_CHECKS_KEY } from '../../guide/bossChecksModel';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';

/**
 * Antigravity CLI login — URL + paste-back code (contract addendum):
 * POST /cli/login → WS `cli.login` starting → waiting_code {url} → the user
 * pastes the code → POST /cli/login/{id}/code → verifying → done|failed.
 *
 * v0.1.30: WS is not the only channel any more — while a login is open the hook also polls
 * GET /cli/login/{id} every 2 s (before, a missed/blocked WS left the panel stuck on "Đang mở phiên…"
 * with no link, i.e. "đổi tài khoản không hoạt động").
 */
export const CLI_LOGIN_POLL_MS = 2000;

export function useCliLogin(kind: CliKind = 'antigravity_cli') {
  const [loginId, setLoginId] = useState<string | null>(null);
  const ev = useQuery<CliLoginEvent | null>({
    queryKey: qk2.cliLogin(loginId ?? '-'),
    queryFn: async ({ signal }) => {
      if (!loginId) return null;
      try {
        return await api.cli.loginStatus(loginId, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404)
          return { login_id: loginId, status: 'failed', message: 'Phiên đăng nhập đã kết thúc (máy chủ vừa khởi động lại) — bấm “Thử lại”.' };
        throw e;
      }
    },
    enabled: !!loginId,
    initialData: null,
    staleTime: 0,
    retry: false,
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      return st === 'done' || st === 'failed' ? false : CLI_LOGIN_POLL_MS;
    },
  });
  const start = useMutation({
    mutationFn: () => api.cli.login(kind),
    onSuccess: (r) => {
      // The WS event may arrive before or after the 202; seed "starting" only if nothing came yet.
      if (!queryClient.getQueryData(qk2.cliLogin(r.login_id)))
        queryClient.setQueryData<CliLoginEvent>(qk2.cliLogin(r.login_id), { login_id: r.login_id, status: 'starting' });
      setLoginId(r.login_id);
    },
  });
  const submit = useMutation({
    mutationFn: (code: string) => api.cli.submitCode(loginId!, code),
    onSuccess: () =>
      queryClient.setQueryData<CliLoginEvent | null>(qk2.cliLogin(loginId!), (old) =>
        old && old.status === 'waiting_code' ? { ...old, status: 'verifying' } : old,
      ),
  });
  const cancel = useMutation({
    mutationFn: () => (loginId ? api.cli.cancelLogin(loginId) : Promise.resolve()),
    onSettled: () => {
      setLoginId(null);
      start.reset();
      submit.reset();
    },
  });
  const status = ev.data?.status ?? (start.isPending ? 'starting' : null);
  const finished = status === 'done' || status === 'failed';
  useEffect(() => {
    // v0.1.39: máy chủ ghi kết quả kiểm "Đăng nhập" (agy_login/claude_login) TRƯỚC khi báo done/failed → tải lại ô kết
    // quả của "Việc Sếp cần làm" ngay (trang đó không thăm lại khi không có kết quả 'pending').
    if (ev.data?.status === 'done' || ev.data?.status === 'failed') void queryClient.invalidateQueries({ queryKey: BOSS_CHECKS_KEY });
    if (ev.data?.status === 'done') {
      // Polling may see "done" before (or instead of) the WS event: refresh what the WS handler would.
      void queryClient.invalidateQueries({ queryKey: qk2.cliProfiles });
      void queryClient.invalidateQueries({ queryKey: qk2.providers });
      void queryClient.invalidateQueries({ queryKey: qk2.credentials });
      const who = ev.data.profile?.email;
      toast(
        who
          ? `Đã thêm tài khoản ${who} — AI đang dùng tài khoản này.`
          : kind === 'claude_code_cli'
            ? 'Đã đăng nhập tài khoản Claude cho AI.'
            : 'Đã đăng nhập tài khoản Google cho AI.',
      );
      setLoginId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `kind` is fixed for the hook's lifetime
  }, [ev.data]);
  return {
    kind,
    active: !!loginId || start.isPending,
    event: ev.data,
    status,
    finished,
    start,
    submit,
    cancel,
    clear: () => {
      setLoginId(null);
      start.reset();
      submit.reset();
    },
  };
}

export type CliLogin = ReturnType<typeof useCliLogin>;
