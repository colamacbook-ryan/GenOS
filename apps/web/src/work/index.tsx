/** v0.1.78 (G0) — `/work` (Việc): Việc & Nhắc hẹn (việc đến hạn, lời hứa). */
import { HubPage, type HubTab } from '../shell/HubPage';
import { LEGACY_SCREENS } from '../shell/legacyScreens';

const Tasks = LEGACY_SCREENS.tasks;
const TABS: HubTab[] = [{ key: 'all', label: 'Tất cả việc', screen: 'tasks', render: () => <Tasks /> }];

export function WorkPage() {
  return <HubPage base="/work" title="Việc" label="Việc" tabs={TABS} />;
}
