import type { ComponentType } from 'react';
import './queries';

/** Giai đoạn 3 · nền chung: không có màn riêng; các thành phần dùng chung xuất từ đây cho các cụm màn. */
export const SCREENS: Record<string, ComponentType> = {};

export { ConfidenceChip, EvidenceDialog, WhyButton } from './Evidence';
export { SavedViewsButton } from './SavedViews';
export { searchToFilters, viewHref } from './viewsModel';
export { qk3, useDraft, useDrafts, useExplain, useRawQuote, useViews } from './queries';
