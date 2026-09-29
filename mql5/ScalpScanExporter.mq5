//+------------------------------------------------------------------+
//| ScalpScanExporter.mq5                                            |
//| Exports live quotes, contract specs, margin, 1m/5m/15m bars,     |
//| tick statistics and the high-impact economic calendar to a JSON  |
//| file that `scalpscan --source file` reads (macOS / Linux / Wine). |
//| Read-only: this EA never places, modifies or closes orders.      |
//+------------------------------------------------------------------+
#property copyright "Bybit-scalping"
#property version   "1.30"

input int    InpIntervalSec     = 15;    // Export interval, seconds
input string InpNameContains    = "";    // Only symbols containing this text ("" = all)
input bool   InpOnlyMarketWatch = false; // Only symbols already in Market Watch
input bool   InpIncludeStocks   = false; // Also stock CFDs outside Market Watch (Bybit has ~10k)
input int    InpMaxSymbols      = 600;   // Safety cap on exported symbols
input int    InpBarsM1          = 242;   // 1m bars (4h baseline + current)
input int    InpBarsM5          = 60;
input int    InpBarsM15         = 60;
input int    InpTickWindowSec   = 300;   // Window for tick rate / spread statistics
input int    InpCalendarAheadH  = 24;    // Economic calendar look-ahead, hours
input int    InpBudgetSec       = 8;     // Per-cycle time budget for bars/ticks; the rest reuse the last cycle

const string DIR  = "scalpscan";
const string TMP  = "scalpscan\\snapshot.tmp";
const string DEST = "scalpscan\\snapshot.json";

long g_offset = 0; // trade server time - UTC, seconds
bool g_first  = true;
int  g_next   = 0;  // rotation: where the next cycle starts refreshing bars/ticks
uint g_start  = 0;  // GetTickCount() at the start of the current cycle

// bars/ticks JSON from earlier cycles, so a slow symbol never blocks the snapshot
string g_cache_sym[];
string g_cache_heavy[];

int CacheFind(string sym)
  {
   for(int i = 0; i < ArraySize(g_cache_sym); i++)
      if(g_cache_sym[i] == sym)
         return i;
   return -1;
  }

void CachePut(string sym, string heavy)
  {
   int i = CacheFind(sym);
   if(i < 0)
     {
      i = ArraySize(g_cache_sym);
      ArrayResize(g_cache_sym, i + 1);
      ArrayResize(g_cache_heavy, i + 1);
      g_cache_sym[i] = sym;
     }
   g_cache_heavy[i] = heavy;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   FolderCreate(DIR, FILE_COMMON);
   EventSetTimer(2); // first export shortly after start, then every InpIntervalSec
   Print("ScalpScanExporter: started, snapshot -> ", TerminalInfoString(TERMINAL_COMMONDATA_PATH),
         "\\Files\\", DEST);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer()
  {
   Export();
   if(g_first)
     {
      g_first = false;
      EventKillTimer();
      EventSetTimer(MathMax(InpIntervalSec, 5));
     }
  }

//--- JSON helpers ---------------------------------------------------
string JS(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   StringReplace(s, "\t", " ");
   return "\"" + s + "\"";
  }

string JD(double v, int digits = 10)
  {
   if(!MathIsValidNumber(v))
      return "null";
   return DoubleToString(v, digits);
  }

long ToUtc(long server_time) { return server_time - g_offset; }

//--- universe: FX, metals, energy, indices, crypto; stocks only if in Market Watch ---
const string CCY = "USD EUR GBP JPY CHF AUD NZD CAD SEK NOK DKK PLN HUF CZK TRY ZAR MXN SGD HKD CNH CNY";
string NON_STOCK_PREFIXES[] = {
   "XAU", "XAG", "XPT", "XPD", "USO", "UKO", "WTI", "BRENT", "OIL", "NGAS", "NATGAS", "COPPER",
   "GOLD", "SILVER", "US30", "US500", "US100", "SP500", "SPX", "NAS", "NDX", "USTEC", "US2000",
   "DJ30", "GER", "DE30", "DE40", "DAX", "EU50", "STOXX", "FRA", "F40", "CAC", "ESP", "SPA35",
   "IT40", "NL25", "UK100", "FTSE", "JP225", "JPN225", "N225", "NIKKEI", "HK50", "HSI", "CHINA",
   "CN50", "A50", "AUS200", "ASX", "SWI20", "SMI", "VIX", "DXY", "USDX",
   "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB", "DOT", "AVAX", "LINK"};

bool IsNonStock(string sym)
  {
   string path = SymbolInfoString(sym, SYMBOL_PATH);
   StringToLower(path);
   if(StringFind(path, "stock") >= 0 || StringFind(path, "share") >= 0 || StringFind(path, "equit") >= 0)
      return false;
   if(StringFind(path, "forex") >= 0 || StringFind(path, "metal") >= 0 || StringFind(path, "commod") >= 0
      || StringFind(path, "energ") >= 0 || StringFind(path, "indic") >= 0 || StringFind(path, "index") >= 0
      || StringFind(path, "crypto") >= 0)
      return true;
   string name = sym;
   StringToUpper(name);
   for(int i = 0; i < ArraySize(NON_STOCK_PREFIXES); i++)
      if(StringFind(name, NON_STOCK_PREFIXES[i]) == 0)
         return true;
   return StringLen(name) >= 6 && StringFind(CCY, StringSubstr(name, 0, 3)) >= 0
          && StringFind(CCY, StringSubstr(name, 3, 3)) >= 0;
  }

bool Wanted(string sym)
  {
   if(InpNameContains != "" && StringFind(sym, InpNameContains) < 0)
      return false;
   return SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_DISABLED;
  }

//--- bars as [t_utc, o, h, l, c, tick_volume, spread_points] -----------
string BarsJson(string sym, ENUM_TIMEFRAMES tf, int count, int digits)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(sym, tf, 0, count, r);
   if(n <= 0)
      return "[]";
   string out = "[";
   for(int i = 0; i < n; i++)
     {
      if(i > 0) out += ",";
      out += "[" + IntegerToString(ToUtc((long)r[i].time)) + "," + JD(r[i].open, digits) + ","
             + JD(r[i].high, digits) + "," + JD(r[i].low, digits) + "," + JD(r[i].close, digits) + ","
             + IntegerToString(r[i].tick_volume) + "," + IntegerToString(r[i].spread) + "]";
     }
   return out + "]";
  }

//--- tick statistics over the last InpTickWindowSec --------------------
string TickStatsJson(string sym, ulong last_msc)
  {
   if(last_msc == 0)
      return "null";
   MqlTick ticks[];
   ulong from = last_msc - (ulong)InpTickWindowSec * 1000;
   int n = CopyTicksRange(sym, ticks, COPY_TICKS_INFO, from, last_msc);
   if(n <= 0)
      return "null";
   double sp[];
   ArrayResize(sp, n);
   int k = 0;
   for(int i = 0; i < n; i++)
      if(ticks[i].bid > 0 && ticks[i].ask > 0)
         sp[k++] = ticks[i].ask - ticks[i].bid;
   if(k == 0)
      return "null";
   ArrayResize(sp, k);
   ArraySort(sp);
   double med = (k % 2 == 1) ? sp[k / 2] : (sp[k / 2 - 1] + sp[k / 2]) / 2.0;
   double p90 = sp[(int)MathFloor(0.9 * (k - 1))];
   return "{\"window_sec\":" + IntegerToString(InpTickWindowSec) + ",\"count\":" + IntegerToString(n)
          + ",\"spread_median\":" + JD(med) + ",\"spread_p90\":" + JD(p90)
          + ",\"spread_max\":" + JD(sp[k - 1]) + "}";
  }

//--- one symbol -------------------------------------------------------
string SymbolJson(string sym, bool refresh)
  {
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   MqlTick t;
   bool has_tick = SymbolInfoTick(sym, t) && t.bid > 0 && t.ask > 0;

   string margin = "null";
   if(has_tick && vmin > 0)
     {
      double mb = 0, ms = 0;
      bool ob = OrderCalcMargin(ORDER_TYPE_BUY, sym, vmin, t.ask, mb);
      bool os = OrderCalcMargin(ORDER_TYPE_SELL, sym, vmin, t.bid, ms);
      double m = MathMax(ob ? mb : 0, os ? ms : 0);
      if(m > 0)
         margin = JD(m, 4);
     }

   // Bars/ticks: refresh only while within the cycle's time budget, otherwise reuse the previous
   // cycle's data. A request for history that is not loaded yet can block for tens of seconds, so
   // the budget keeps one slow symbol from delaying the whole snapshot (it also starts the download).
   string heavy = "";
   int ci = CacheFind(sym);
   bool in_budget = GetTickCount() - g_start < (uint)InpBudgetSec * 1000;
   if(refresh && in_budget && TerminalInfoInteger(TERMINAL_CONNECTED))
     {
      heavy = ",\"ticks\":" + (has_tick ? TickStatsJson(sym, t.time_msc) : "null")
              + ",\"m1\":" + BarsJson(sym, PERIOD_M1, InpBarsM1, digits)
              + ",\"m5\":" + BarsJson(sym, PERIOD_M5, InpBarsM5, digits)
              + ",\"m15\":" + BarsJson(sym, PERIOD_M15, InpBarsM15, digits);
      CachePut(sym, heavy);
     }
   else if(ci >= 0)
      heavy = g_cache_heavy[ci];
   else
      heavy = ",\"ticks\":null,\"m1\":[],\"m5\":[],\"m15\":[]"; // history still loading

   string s = "{\"name\":" + JS(sym)
      + ",\"description\":" + JS(SymbolInfoString(sym, SYMBOL_DESCRIPTION))
      + ",\"path\":" + JS(SymbolInfoString(sym, SYMBOL_PATH))
      + ",\"digits\":" + IntegerToString(digits)
      + ",\"point\":" + JD(SymbolInfoDouble(sym, SYMBOL_POINT))
      + ",\"tick_size\":" + JD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE))
      + ",\"tick_value\":" + JD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE))
      + ",\"tick_value_loss\":" + JD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS))
      + ",\"tick_value_profit\":" + JD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_PROFIT))
      + ",\"contract_size\":" + JD(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE))
      + ",\"volume_min\":" + JD(vmin)
      + ",\"volume_step\":" + JD(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP))
      + ",\"volume_max\":" + JD(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX))
      + ",\"currency_base\":" + JS(SymbolInfoString(sym, SYMBOL_CURRENCY_BASE))
      + ",\"currency_profit\":" + JS(SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT))
      + ",\"currency_margin\":" + JS(SymbolInfoString(sym, SYMBOL_CURRENCY_MARGIN))
      + ",\"swap_long\":" + JD(SymbolInfoDouble(sym, SYMBOL_SWAP_LONG), 4)
      + ",\"swap_short\":" + JD(SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT), 4)
      + ",\"swap_mode\":" + IntegerToString(SymbolInfoInteger(sym, SYMBOL_SWAP_MODE))
      + ",\"trade_mode\":" + IntegerToString(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE))
      + ",\"bid\":" + (has_tick ? JD(t.bid, digits) : "null")
      + ",\"ask\":" + (has_tick ? JD(t.ask, digits) : "null")
      + ",\"quote_utc_ms\":" + (has_tick ? IntegerToString((long)t.time_msc - g_offset * 1000) : "null")
      + ",\"margin_min_lot\":" + margin
      + heavy
      + "}";
   return s;
  }

//--- high-importance economic calendar (MT5 built-in) --------------------
string CalendarJson()
  {
   MqlCalendarValue values[];
   datetime now = TimeTradeServer();
   if(!CalendarValueHistory(values, now - 3600, now + InpCalendarAheadH * 3600))
      return "null"; // calendar unavailable
   int n = ArraySize(values);
   string out = "[";
   int written = 0;
   for(int i = 0; i < n; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev) || ev.importance != CALENDAR_IMPORTANCE_HIGH)
         continue;
      MqlCalendarCountry c;
      if(!CalendarCountryById(ev.country_id, c))
         continue;
      if(written++ > 0) out += ",";
      out += "{\"t\":" + IntegerToString(ToUtc((long)values[i].time)) + ",\"currency\":" + JS(c.currency)
             + ",\"title\":" + JS(ev.name) + ",\"impact\":\"High\"}";
     }
   return out + "]";
  }

//--- main export ------------------------------------------------------
void Export()
  {
   g_start = GetTickCount();
   bool connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   long off = (long)TimeTradeServer() - (long)TimeGMT();
   g_offset = (long)MathRound(off / 900.0) * 900;

   int h = FileOpen(TMP, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
     {
      Print("ScalpScanExporter: cannot open file, error ", GetLastError());
      return;
     }
   FileWriteString(h, "{\"schema\":1,\"exporter\":\"ScalpScanExporter 1.30\""
      + ",\"connected\":" + (connected ? "true" : "false")
      + ",\"ping_ms\":" + IntegerToString(TerminalInfoInteger(TERMINAL_PING_LAST) / 1000)
      + ",\"generated_utc\":" + IntegerToString((long)TimeGMT())
      + ",\"server_offset_sec\":" + IntegerToString(g_offset)
      + ",\"tick_window_sec\":" + IntegerToString(InpTickWindowSec)
      + ",\"account\":{\"login\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
      + ",\"server\":" + JS(AccountInfoString(ACCOUNT_SERVER))
      + ",\"company\":" + JS(AccountInfoString(ACCOUNT_COMPANY))
      + ",\"currency\":" + JS(AccountInfoString(ACCOUNT_CURRENCY))
      + ",\"leverage\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE))
      + ",\"balance\":" + JD(AccountInfoDouble(ACCOUNT_BALANCE), 2)
      + ",\"free_margin\":" + JD(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)
      + ",\"trade_mode\":" + IntegerToString(AccountInfoInteger(ACCOUNT_TRADE_MODE)) + "}"
      + ",\"calendar\":" + CalendarJson()
      + ",\"symbols\":[");

   // Pass 1: everything already in Market Watch (the user's own picks, stocks included).
   string picked[];
   int watch = SymbolsTotal(true);
   for(int i = 0; i < watch; i++)
     {
      string sym = SymbolName(i, true);
      if(Wanted(sym))
        {
         int n = ArraySize(picked);
         ArrayResize(picked, n + 1);
         picked[n] = sym;
        }
     }
   // Pass 2: the rest of the server's FX / metals / energy / indices / crypto (stocks on request).
   // Symbols outside Market Watch receive no live ticks, so they are added to it.
   if(!InpOnlyMarketWatch)
     {
      int total = SymbolsTotal(false);
      for(int i = 0; i < total && ArraySize(picked) < InpMaxSymbols; i++)
        {
         string sym = SymbolName(i, false);
         if(SymbolInfoInteger(sym, SYMBOL_SELECT) || !Wanted(sym))
            continue;
         if(!InpIncludeStocks && !IsNonStock(sym))
            continue;
         SymbolSelect(sym, true);
         int n = ArraySize(picked);
         ArrayResize(picked, n + 1);
         picked[n] = sym;
        }
     }
   // Refresh bars/ticks starting at a rotating position so every symbol gets fresh data in turn.
   int total_picked = MathMin(ArraySize(picked), InpMaxSymbols);
   if(g_next >= total_picked)
      g_next = 0;
   string parts[];
   ArrayResize(parts, total_picked);
   int refreshed = 0;
   for(int k = 0; k < total_picked; k++)
     {
      int i = (g_next + k) % total_picked;
      bool before = GetTickCount() - g_start < (uint)InpBudgetSec * 1000;
      parts[i] = SymbolJson(picked[i], true);
      if(before)
         refreshed++;
     }
   g_next = (g_next + refreshed) % MathMax(total_picked, 1);
   int written = 0;
   for(int i = 0; i < total_picked; i++)
      FileWriteString(h, (written++ > 0 ? "," : "") + parts[i]);
   FileWriteString(h, "]}");
   FileClose(h);

   if(!FileMove(TMP, FILE_COMMON, DEST, FILE_COMMON | FILE_REWRITE))
      Print("ScalpScanExporter: cannot move snapshot, error ", GetLastError());
   Comment("ScalpScanExporter: ", written, " symbols exported at ", TimeToString(TimeGMT(), TIME_SECONDS),
           " UTC (", (GetTickCount() - g_start) / 1000.0, " s)",
           connected ? "" : "\nMT5 NOT CONNECTED to the trade server - check login / server (bottom-right corner)");
   if(!connected)
      Print("ScalpScanExporter: terminal is not connected to the trade server - data is not live");
  }
//+------------------------------------------------------------------+
