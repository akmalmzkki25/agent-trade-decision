//+------------------------------------------------------------------+
//| QlipV6/Calendar.mqh                                              |
//| USD events from the MT5 economic calendar, converted to UTC.     |
//|                                                                  |
//| Calendar times are trade-server times. Event names and codes are |
//| broker-supplied text and are reduced to the character sets the   |
//| adapter accepts before they are written.                         |
//+------------------------------------------------------------------+
#ifndef QLIPV6_CALENDAR_MQH
#define QLIPV6_CALENDAR_MQH

#include "Json.mqh"
#include "Market.mqh"

#define CALENDAR_CURRENCY      "USD"
#define CALENDAR_CODE_MAX      60
#define CALENDAR_NAME_MAX      80
#define CALENDAR_MAX_EVENTS    50
#define CALENDAR_VALUE_DIGITS  6

string CalendarImportanceName(const ENUM_CALENDAR_EVENT_IMPORTANCE importance)
{
   switch(importance)
   {
      case CALENDAR_IMPORTANCE_LOW:      return "LOW";
      case CALENDAR_IMPORTANCE_MODERATE: return "MODERATE";
      case CALENDAR_IMPORTANCE_HIGH:     return "HIGH";
      default:                           return "NONE";
   }
}

// The code must match [a-z0-9-]{1,60}; an event whose code sanitises to
// nothing is still identifiable by its numeric id.
string CalendarEventCode(const MqlCalendarEvent &event)
{
   string code = SlugCode(event.event_code, CALENDAR_CODE_MAX);
   if(code == "")
      code = "event-" + IntegerToString((long)event.id);
   return code;
}

void AddOptionalValue(CJsonObject &o, const string key, const bool present, const double value)
{
   if(present && MathIsValidNumber(value))
      o.AddNum(key, value, CALENDAR_VALUE_DIGITS);
   else
      o.AddNull(key);
}

string CalendarEventJson(MqlCalendarValue &value, const MqlCalendarEvent &event, const int offset_s)
{
   CJsonObject o;
   o.AddInt("event_id", (long)event.id);
   o.AddInt("time_epoch", ServerToUtc(value.time, offset_s));
   o.AddStr("currency", CALENDAR_CURRENCY);
   o.AddStr("importance", CalendarImportanceName(event.importance));
   o.AddStr("code", CalendarEventCode(event));
   o.AddStr("name", SanitizeAscii(event.name, CALENDAR_NAME_MAX));
   AddOptionalValue(o, "actual", value.HasActualValue(), value.GetActualValue());
   AddOptionalValue(o, "forecast", value.HasForecastValue(), value.GetForecastValue());
   AddOptionalValue(o, "previous", value.HasPreviousValue(), value.GetPreviousValue());
   return o.Text();
}

// Copies USD calendar values in [from, from + horizon). Returns -1 when the
// calendar could not be read, which is not the same as "no events".
int CalendarUsdValues(const datetime from_server, const int horizon_s, MqlCalendarValue &values[])
{
   ResetLastError();
   if(!CalendarValueHistory(values, from_server, from_server + horizon_s, NULL, CALENDAR_CURRENCY))
   {
      int error = GetLastError();
      if(error != 0)
      {
         PrintFormat("V6 calendar: CalendarValueHistory failed (err=%d)", error);
         return -1;
      }
   }
   return ArraySize(values);
}

// High-impact USD events from now to `horizon_s` ahead, as a JSON array.
string CalendarHighUsdJson(const datetime now_server, const int horizon_s, const int offset_s)
{
   MqlCalendarValue values[];
   int n = CalendarUsdValues(now_server, horizon_s, values);
   CJsonArray events;
   for(int i = 0; i < n && events.Count() < CALENDAR_MAX_EVENTS; i++)
   {
      MqlCalendarEvent event;
      if(!CalendarEventById(values[i].event_id, event))
         continue;
      if(event.importance != CALENDAR_IMPORTANCE_HIGH || values[i].time < now_server)
         continue;
      events.AddRaw(CalendarEventJson(values[i], event, offset_s));
   }
   return events.Text();
}

// Every USD event, any importance, in the horizon; 0 when unreadable.
int CalendarUsdEventCount(const datetime now_server, const int horizon_s)
{
   MqlCalendarValue values[];
   return MathMax(CalendarUsdValues(now_server, horizon_s, values), 0);
}

#endif // QLIPV6_CALENDAR_MQH
