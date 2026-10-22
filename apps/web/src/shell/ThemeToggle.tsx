import { IconButton } from '@gen-harness/ui';
import { useMe } from '../lib/queries';
import { nextTheme, THEME_ICON, THEME_LABEL, useThemePref } from '../lib/theme';
import { useUiStore } from '../lib/uiStore';

/** B7: nút đổi giao diện ở header — vòng Theo hệ thống → Sáng → Tối. Lưu theo từng người dùng (localStorage). */
export function ThemeToggle() {
  const me = useMe();
  const id = me.data?.id ?? null;
  const pref = useThemePref(id);
  const setTheme = useUiStore((s) => s.setTheme);
  const next = nextTheme(pref);
  return (
    <IconButton
      icon={THEME_ICON[pref]}
      label={`Giao diện: ${THEME_LABEL[pref]} — bấm để chuyển sang ${THEME_LABEL[next]}`}
      className="hd-theme"
      data-theme-pref={pref}
      onClick={() => setTheme(next, id)}
    />
  );
}
