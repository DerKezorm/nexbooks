/**
 * The choice of the market at the head of Discover and of an opened shelf (the market beside the
 * title): the four main markets as buttons, the others the server offers in a list beside them, and the one in use
 * among the buttons even when it is one of those.
 */
import { useTranslation } from 'react-i18next'

import { marketOrder } from '../lib/discover'

export function MarketPick({ current, markets, onPick }: { current: string; markets: string[]; onPick: (market: string) => void }) {
  const { t } = useTranslation()
  const ordered = marketOrder(markets)
  const shown = ordered.filter((code, index) => index < 4 || code === current)
  const others = ordered.filter((code) => !shown.includes(code))
  return (
    <div className="market-pick">
      <div className="seg" id="market" role="group" aria-label={t('discover.market')}>
        {shown.map((code) => (
          <button key={code} type="button" data-m={code} className={current === code ? 'on' : ''} aria-pressed={current === code} onClick={() => onPick(code)}>
            {t(`discover.marketShort.${code}`, { defaultValue: t(`discover.markets.${code}`) })}
          </button>
        ))}
      </div>
      {others.length > 0 && (
        <select className="market-more" aria-label={t('discover.moreMarkets')} value="" onChange={(event) => event.target.value && onPick(event.target.value)}>
          <option value="">{t('discover.moreMarkets')}</option>
          {others.map((code) => (
            <option key={code} value={code}>
              {t(`discover.markets.${code}`, { defaultValue: code })}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
