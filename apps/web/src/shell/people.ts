import type { Me } from '@gen-harness/contracts';
import { roleLabel } from '../screens/system/systemModel';

const HONORIFICS = new Set(['anh', 'chị', 'chi', 'em', 'ông', 'bà', 'cô', 'chú', 'bác', 'sếp']);

/**
 * Avatar initials: drop a leading honorific and any "(nickname)", then take the
 * first letters of the last two words — "Anh Nguyễn Văn A (Chủ)" → "VA" (design).
 */
export function initials(displayName: string): string {
  const words = displayName
    .replace(/\([^)]*\)/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  while (words.length > 1 && HONORIFICS.has(words[0].toLocaleLowerCase('vi'))) words.shift();
  const pick = words.length >= 2 ? words.slice(-2) : words;
  return pick.map((w) => w[0]!.toLocaleUpperCase('vi')).join('') || '·';
}

/** Footer second line — design shows "Owner · thấy toàn cảnh" for the Owner. */
export function roleLine(me: Pick<Me, 'role'>): string {
  if (me.role.code === 'owner') return 'Owner · thấy toàn cảnh';
  return roleLabel(me.role.code, me.role.name);
}
