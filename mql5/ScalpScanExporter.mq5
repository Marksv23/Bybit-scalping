//+------------------------------------------------------------------+
//| ScalpScanExporter.mq5                                            |
//| Exports live quotes, contract specs, margin, 1m/5m/15m bars,     |
//| tick statistics and the high-impact economic calendar to a JSON  |
//| file that `scalpscan --source file` reads (macOS / Linux / Wine). |
//| Read-only: this EA never places, modifies or closes orders.      |
//+------------------------------------------------------------------+
#property copyright "Bybit-scalping"
#property version   "1.00"

input int    InpIntervalSec     = 15;    // Export interval, seconds
input string InpNameContains    = "";    // Only symbols containing this text ("" = all, e.g. ".s")
input bool   InpOnlyMarketWatch = false; // Only symbols already in Market Watch
input int    InpMaxSymbols      = 400;   // Safety cap on exported symbols
input int    InpBarsM1          = 242;   // 1m bars (4h baseline + current)
input int    InpBarsM5          = 60;
input int    InpBarsM15         = 60;
input int    InpTickWindowSec   = 300;   // Window for tick rate / spread statistics
input int    InpCalendarAheadH  = 24;    // Economic calendar look-ahead, hours

const string DIR  = "scalpscan";
const string TMP  = "scalpscan\\snapshot.tmp";
const string DEST = "scalpscan\\snapshot.json";

long g_offset = 0; // trade server time - UTC, seconds

//+------------------------------------------------------------------+
int OnInit()
  {
   FolderCreate(DIR, FILE_COMMON);
   EventSetTimer(MathMax(InpIntervalSec, 5));
   Export();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { Export(); }

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
string SymbolJson(string sym)
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
      + ",\"ticks\":" + (has_tick ? TickStatsJson(sym, t.time_msc) : "null")
      + ",\"m1\":" + BarsJson(sym, PERIOD_M1, InpBarsM1, digits)
      + ",\"m5\":" + BarsJson(sym, PERIOD_M5, InpBarsM5, digits)
      + ",\"m15\":" + BarsJson(sym, PERIOD_M15, InpBarsM15, digits)
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
   long off = (long)TimeTradeServer() - (long)TimeGMT();
   g_offset = (long)MathRound(off / 900.0) * 900;

   int h = FileOpen(TMP, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
     {
      Print("ScalpScanExporter: cannot open file, error ", GetLastError());
      return;
     }
   FileWriteString(h, "{\"schema\":1,\"exporter\":\"ScalpScanExporter 1.00\""
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

   int total = SymbolsTotal(InpOnlyMarketWatch);
   int written = 0;
   for(int i = 0; i < total && written < InpMaxSymbols; i++)
     {
      string sym = SymbolName(i, InpOnlyMarketWatch);
      if(InpNameContains != "" && StringFind(sym, InpNameContains) < 0)
         continue;
      if(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED)
         continue;
      // Symbols outside Market Watch do not receive live ticks, so keep them selected.
      if(!SymbolInfoInteger(sym, SYMBOL_SELECT))
         SymbolSelect(sym, true);
      FileWriteString(h, (written++ > 0 ? "," : "") + SymbolJson(sym));
     }
   FileWriteString(h, "]}");
   FileClose(h);

   if(!FileMove(TMP, FILE_COMMON, DEST, FILE_COMMON | FILE_REWRITE))
      Print("ScalpScanExporter: cannot move snapshot, error ", GetLastError());
   Comment("ScalpScanExporter: ", written, " symbols exported at ", TimeToString(TimeGMT(), TIME_SECONDS), " UTC");
  }
//+------------------------------------------------------------------+
