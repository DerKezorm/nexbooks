/**
 * Settings, Fetching (the operator's): the part of the settings where nexbooks is told where books come from and how they
 * are fetched. Five tabs: Indexers, Download programs, Formats and places, Automation, Rights. The tab is
 * in the address (`?tab=procure&sub=clients`), so a link can point at one.
 */
import { Bolt, Search, Server, SlidersHorizontal, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { StartGate } from '../../../components/procure/StartDialog'
import { TabRow, type Tab } from '../ui'
import { AutomationPart } from './AutomationPart'
import { ClientsPart } from './ClientsPart'
import { FormatsPart } from './FormatsPart'
import { IndexersPart } from './IndexersPart'
import { RightsPart } from './RightsPart'

const SUBS = ['indexers', 'clients', 'formats', 'automation', 'rights'] as const
type Sub = (typeof SUBS)[number]
const ICON = { indexers: Search, clients: Server, formats: SlidersHorizontal, automation: Bolt, rights: Users }

export function ProcurePart() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const asked = params.get('sub') as Sub | null
  const sub: Sub = asked && SUBS.includes(asked) ? asked : 'indexers'
  const tabs: Tab<Sub>[] = SUBS.map((value) => ({ value, label: t(`procure.tabs.${value}`), icon: ICON[value] }))
  return (
    <div className="space-y-6">
      <StartGate />
      <p className="text-sm text-ink-2" data-testid="procure-note">{t('procure.text')}</p>
      <TabRow tabs={tabs} active={sub} under label={t('procure.title')} onChange={(value) => setParams({ tab: 'procure', ...(value === 'indexers' ? {} : { sub: value }) }, { replace: true })} />
      <div className="space-y-6 pt-1">
        {sub === 'indexers' && <IndexersPart />}
        {sub === 'clients' && <ClientsPart />}
        {sub === 'formats' && <FormatsPart />}
        {sub === 'automation' && <AutomationPart />}
        {sub === 'rights' && <RightsPart />}
      </div>
    </div>
  )
}
