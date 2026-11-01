/**
 * Settings, for the operator only: the server in one row of tabs (accounts, sign-in, mail, Web Push, backup, languages,
 * log, API), with the libraries after the accounts. Everything personal is under "My account". The part is in the
 * address (`?tab=signin`), so a link can point at one. A member has no use for this page and is sent to their account.
 */
import { BellRing, FolderTree, Globe, HardDrive, KeyRound, Languages, Library, Mail, PackageSearch, Plug, ScrollText, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Navigate, useSearchParams } from 'react-router-dom'

import { PageHead } from '../components/PageHead'
import { useAuth } from '../state/auth'
import { LibrariesPart } from './settings/LibraryCards'
import { OrganizePart } from './settings/OrganizeCards'
import { ProcurePart } from './settings/procure/ProcurePart'
import { PushServerCard } from './settings/PushCards'
import { ReadinessCard } from './settings/ReadinessCards'
import { AccountsCard, ApiKeysCard, BackupsCard, EreadersCard, InterfaceCard, LanguagesCard, LogCard, MailCard, useServerSettings } from './settings/ServerCards'
import { SignInPart } from './settings/SignInCards'
import { SourcesPart } from './settings/SourcesCards'
import { StorageCard } from './settings/StorageCards'
import { TabRow, type Tab } from './settings/ui'

type Part = 'accounts' | 'libraries' | 'organize' | 'sources' | 'procure' | 'signin' | 'mail' | 'push' | 'backup' | 'languages' | 'log' | 'api'
const PARTS: Part[] = ['accounts', 'libraries', 'organize', 'sources', 'procure', 'signin', 'mail', 'push', 'backup', 'languages', 'log', 'api']
const PART_ICON = { accounts: Users, libraries: Library, organize: FolderTree, sources: Globe, procure: PackageSearch, signin: KeyRound, mail: Mail, push: BellRing, backup: HardDrive, languages: Languages, log: ScrollText, api: Plug }

export function SettingsPage() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [params, setParams] = useSearchParams()
  if (!me) return null
  if (me.role !== 'operator') return <Navigate to="/account" replace />
  const asked = params.get('tab') as Part | null
  const part: Part = asked && PARTS.includes(asked) ? asked : 'accounts'
  const tabs: Tab<Part>[] = PARTS.map((value) => ({ value, label: t(`settings.parts.${value}`), icon: PART_ICON[value] }))
  return (
    <div className="view">
      <PageHead title={t('settings.title')} text={t('settings.text')} />
      <div className="page space-y-5">
        <TabRow tabs={tabs} active={part} onChange={(value) => setParams(value === 'accounts' ? {} : { tab: value }, { replace: true })} label={t('settings.title')} />
        <div className="space-y-6 pt-1">
          <ServerPart part={part} />
        </div>
      </div>
    </div>
  )
}

/** One part of the server, for the operator. */
function ServerPart({ part }: { part: Part }) {
  const server = useServerSettings()
  switch (part) {
    case 'accounts':
      return <AccountsCard />
    case 'libraries':
      return <LibrariesPart server={server} />
    case 'organize':
      return <OrganizePart />
    case 'sources':
      return <SourcesPart />
    case 'procure':
      return <ProcurePart />
    case 'signin':
      return (
        <div className="space-y-6">
          {/* nexbooks's own look at the server (its points say "below"), then the three cards of the shared sign-in module,
          one below the other. */}
          <ReadinessCard />
          <SignInPart server={server} />
        </div>
      )
    case 'mail':
      return <MailCard server={server} />
    case 'push':
      return <PushServerCard />
    case 'backup':
      return (
        <div className="space-y-6">
          <BackupsCard server={server} />
          <StorageCard />
        </div>
      )
    case 'languages':
      return <LanguagesCard />
    case 'log':
      return <LogCard />
    case 'api':
      return (
        <div className="space-y-6">
          <InterfaceCard />
          <ApiKeysCard server={server} />
          <EreadersCard server={server} />
        </div>
      )
  }
}
