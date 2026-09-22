## Exact current inventory (all 140 configured records)

Source token identifies provider and board. All 129 ATS entries are active by membership; country metadata is absent. Five feeds are enabled; six optional employer entries are disabled. Endpoints/configuration and every requested field are in [the full CSV](current-free-inventory.csv) and [JSON](current-free-inventory.json). Here U is the current engine identity within that source; F14 is dated within 14 days as of 2026-09-21. Eligible and relevant counts per real candidate are UNKNOWN for every row. A successful fetch with F14=0 is not proof jobs have closed. Probe seconds include parsing for ATS and feed work.

| Source / board | Company | Enabled | Fetch | Raw | F14 | U | Within-source identity collapse | Seconds |
|---|---|---|---|---:|---:|---:|---:|---:|
| lever:paytm | Paytm | yes | OK | 203 | 47 | 185 | 8.9% | 6.65 |
| lever:meesho | Meesho | yes | OK | 49 | 4 | 48 | 2.0% | 2.92 |
| lever:mindtickle | Mindtickle | yes | OK | 19 | 3 | 19 | 0.0% | 2.29 |
| lever:hevodata | Hevo Data | yes | OK | 48 | 5 | 45 | 6.2% | 2.59 |
| lever:zeta | Zeta | yes | OK | 21 | 5 | 20 | 4.8% | 2.67 |
| lever:fampay | FamPay | yes | OK | 14 | 0 | 14 | 0.0% | 4.09 |
| lever:cred | CRED | yes | OK | 11 | 0 | 11 | 0.0% | 1.98 |
| lever:coderio | Coderio | yes | OK | 24 | 5 | 24 | 0.0% | 2.90 |
| lever:rws | RWS | yes | OK | 67 | 15 | 67 | 0.0% | 3.22 |
| lever:dozee | Dozee | yes | OK | 20 | 5 | 15 | 25.0% | 2.53 |
| lever:pocketfm | Pocket FM | yes | OK | 6 | 2 | 6 | 0.0% | 1.80 |
| lever:veeva | Veeva Systems | yes | OK | 918 | 50 | 447 | 51.3% | 7.14 |
| lever:portagepointpartners | Portage Point Partners | yes | OK | 52 | 2 | 51 | 1.9% | 2.66 |
| lever:acceldata | Acceldata | yes | OK | 41 | 0 | 37 | 9.8% | 2.62 |
| lever:sophos | Sophos | yes | OK | 88 | 13 | 80 | 9.1% | 3.35 |
| lever:levelai | Level AI | yes | OK | 19 | 0 | 19 | 0.0% | 2.35 |
| lever:jumpcloud | JumpCloud | yes | OK | 22 | 8 | 22 | 0.0% | 2.62 |
| lever:appzen | AppZen | yes | OK | 19 | 2 | 19 | 0.0% | 2.58 |
| lever:cin7 | Cin7 | yes | OK | 8 | 1 | 7 | 12.5% | 2.01 |
| lever:binance | Binance | yes | OK | 313 | 39 | 306 | 2.2% | 6.34 |
| lever:biorender | BioRender | yes | OK | 1 | 0 | 1 | 0.0% | 1.64 |
| greenhouse:groww | Groww | yes | OK | 7 | 4 | 7 | 0.0% | 0.39 |
| greenhouse:postman | Postman | yes | FAIL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 0.34 |
| greenhouse:druva | Druva | yes | OK | 39 | 16 | 35 | 10.3% | 0.49 |
| greenhouse:slice | Slice | yes | OK | 27 | 18 | 21 | 22.2% | 0.45 |
| greenhouse:gitlab | GitLab | yes | OK | 213 | 213 | 198 | 7.0% | 1.81 |
| greenhouse:databricks | Databricks | yes | OK | 874 | 240 | 593 | 32.2% | 3.05 |
| greenhouse:twilio | Twilio | yes | OK | 142 | 142 | 122 | 14.1% | 2.30 |
| greenhouse:mongodb | MongoDB | yes | OK | 399 | 399 | 250 | 37.3% | 1.69 |
| greenhouse:elastic | Elastic | yes | OK | 357 | 357 | 188 | 47.3% | 9.28 |
| greenhouse:datadog | Datadog | yes | OK | 454 | 454 | 354 | 22.0% | 3.52 |
| greenhouse:cloudflare | Cloudflare | yes | OK | 381 | 142 | 364 | 4.5% | 3.66 |
| greenhouse:stripe | Stripe | yes | OK | 670 | 670 | 604 | 9.9% | 1.46 |
| greenhouse:netradyne | Netradyne | yes | OK | 26 | 6 | 26 | 0.0% | 0.42 |
| greenhouse:figma | Figma | yes | OK | 153 | 27 | 153 | 0.0% | 2.50 |
| greenhouse:roku | Roku | yes | OK | 255 | 239 | 166 | 34.9% | 1.73 |
| greenhouse:flix | Flix | yes | OK | 153 | 79 | 126 | 17.6% | 1.50 |
| greenhouse:sumup | SumUp | yes | OK | 372 | 250 | 197 | 47.0% | 1.77 |
| greenhouse:ubiquiti | Ubiquiti | yes | OK | 173 | 19 | 151 | 12.7% | 0.52 |
| greenhouse:justworks | Justworks | yes | OK | 97 | 39 | 88 | 9.3% | 0.70 |
| greenhouse:tide | Tide | yes | OK | 80 | 80 | 49 | 38.8% | 0.77 |
| greenhouse:bitwarden | Bitwarden | yes | OK | 44 | 24 | 41 | 6.8% | 0.41 |
| greenhouse:nice | NICE | yes | OK | 162 | 59 | 141 | 13.0% | 1.05 |
| greenhouse:towerresearchcapital | Tower Research Capital | yes | OK | 89 | 16 | 78 | 12.4% | 0.48 |
| greenhouse:dunnhumby | dunnhumby | yes | OK | 34 | 29 | 33 | 2.9% | 1.37 |
| greenhouse:elsevier | Elsevier | yes | OK | 9 | 0 | 7 | 22.2% | 0.40 |
| greenhouse:iris | Iris Software | yes | OK | 2 | 0 | 2 | 0.0% | 0.39 |
| greenhouse:wise | Wise | yes | OK | 17 | 2 | 17 | 0.0% | 0.62 |
| greenhouse:capco | Capco | yes | OK | 721 | 721 | 578 | 19.8% | 2.37 |
| greenhouse:wppproduction | WPP Production | yes | OK | 152 | 52 | 133 | 12.5% | 0.62 |
| greenhouse:stratainformationgroup | Strata Information Group | yes | OK | 11 | 1 | 11 | 0.0% | 0.40 |
| greenhouse:indigo | Indigo | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| greenhouse:mcafee | McAfee, Inc. | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| greenhouse:unisonconsulting | Unison Consulting | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| greenhouse:victrix | Victrix Systems & Labs | yes | OK | 3 | 2 | 3 | 0.0% | 0.34 |
| greenhouse:okta | Okta | yes | OK | 325 | 325 | 288 | 11.4% | 0.87 |
| greenhouse:payoneer | Payoneer | yes | OK | 119 | 119 | 112 | 5.9% | 4.59 |
| greenhouse:accordionindia | Accordion India | yes | OK | 21 | 0 | 20 | 4.8% | 0.41 |
| greenhouse:netskope | Netskope | yes | OK | 142 | 54 | 90 | 36.6% | 0.65 |
| greenhouse:avathon | Avathon | yes | OK | 37 | 4 | 37 | 0.0% | 0.43 |
| greenhouse:newrelic | New Relic | yes | OK | 49 | 49 | 38 | 22.4% | 0.92 |
| greenhouse:cloudsek | CloudSEK | yes | OK | 15 | 3 | 15 | 0.0% | 0.42 |
| greenhouse:komodohealth | Komodo Health | yes | OK | 29 | 10 | 28 | 3.4% | 0.53 |
| greenhouse:godaddy | GoDaddy | yes | OK | 38 | 23 | 35 | 7.9% | 0.48 |
| greenhouse:precisionaq | Precision AQ | yes | OK | 42 | 42 | 40 | 4.8% | 0.44 |
| greenhouse:launchdarkly | LaunchDarkly | yes | OK | 52 | 26 | 51 | 1.9% | 0.45 |
| greenhouse:bitgo | BitGo | yes | OK | 39 | 10 | 30 | 23.1% | 0.45 |
| greenhouse:eulerity | Eulerity | yes | OK | 16 | 4 | 16 | 0.0% | 0.41 |
| greenhouse:rtingscom | RTINGS.com | yes | OK | 5 | 3 | 5 | 0.0% | 0.43 |
| greenhouse:fingerprint | Fingerprint | yes | OK | 29 | 29 | 28 | 3.4% | 1.71 |
| greenhouse:breezeway | Breezeway | yes | OK | 9 | 2 | 8 | 11.1% | 0.43 |
| greenhouse:diligent | Diligent | yes | OK | 6 | 1 | 6 | 0.0% | 0.33 |
| greenhouse:cobblestoneenergy | Cobblestone Energy | yes | OK | 2 | 0 | 2 | 0.0% | 0.41 |
| greenhouse:shield | SHIELD | yes | OK | 1 | 0 | 1 | 0.0% | 0.41 |
| greenhouse:bold | BOLD | yes | OK | 1 | 0 | 1 | 0.0% | 0.36 |
| ashby:linear | Linear | yes | OK | 32 | 3 | 25 | 21.9% | 0.96 |
| ashby:ramp | Ramp | yes | OK | 148 | 24 | 146 | 1.4% | 1.90 |
| ashby:openai | OpenAI | yes | OK | 815 | 135 | 767 | 5.9% | 4.09 |
| ashby:notion | Notion | yes | OK | 128 | 8 | 118 | 7.8% | 1.25 |
| ashby:teero | Teero | yes | OK | 4 | 0 | 3 | 25.0% | 0.79 |
| ashby:clickhouse | ClickHouse | yes | OK | 201 | 40 | 127 | 36.8% | 50.62 |
| ashby:tekion | Tekion | yes | OK | 117 | 19 | 86 | 26.5% | 1.54 |
| ashby:gradera | Gradera | yes | OK | 6 | 2 | 6 | 0.0% | 1.44 |
| ashby:whisk | Whisk Software Private Limited | yes | OK | 4 | 0 | 4 | 0.0% | 1.08 |
| ashby:elevenlabs | ElevenLabs | yes | OK | 226 | 10 | 226 | 0.0% | 0.30 |
| ashby:uipath | UiPath | yes | OK | 101 | 18 | 86 | 14.9% | 83.91 |
| ashby:glomo | Glomo | yes | OK | 7 | 0 | 7 | 0.0% | 0.61 |
| ashby:clera | Clera | yes | OK | 274 | 201 | 183 | 33.2% | 0.74 |
| ashby:abound | Abound | yes | OK | 18 | 3 | 18 | 0.0% | 0.46 |
| ashby:maincode | Maincode | yes | OK | 10 | 6 | 9 | 10.0% | 1.35 |
| ashby:xero | Xero | yes | OK | 105 | 39 | 77 | 26.7% | 24.43 |
| ashby:solace | Solace | yes | OK | 26 | 5 | 26 | 0.0% | 1.70 |
| ashby:brainco | Brain Co. | yes | OK | 35 | 2 | 30 | 14.3% | 1.60 |
| ashby:realmalliance | Realm Alliance | yes | OK | 11 | 0 | 11 | 0.0% | 0.48 |
| ashby:nory | Nory | yes | OK | 6 | 1 | 6 | 0.0% | 2.10 |
| ashby:freetrade | Freetrade | yes | OK | 7 | 2 | 7 | 0.0% | 2.99 |
| ashby:omni | Omni | yes | OK | 23 | 22 | 23 | 0.0% | 0.78 |
| ashby:attio | Attio | yes | OK | 41 | 8 | 22 | 46.3% | 1.44 |
| ashby:pylon | Pylon | yes | OK | 17 | 5 | 17 | 0.0% | 0.51 |
| ashby:vantage | Vantage | yes | OK | 5 | 0 | 5 | 0.0% | 0.63 |
| ashby:sitemate | Sitemate | yes | OK | 14 | 7 | 12 | 14.3% | 0.56 |
| ashby:pilgrim | Pilgrim | yes | OK | 4 | 0 | 4 | 0.0% | 0.42 |
| smartrecruiters:jitterbit | Jitterbit | yes | OK | 24 | 1 | 15 | 37.5% | 0.42 |
| smartrecruiters:renesaselectronics | Renesas Electronics | yes | OK | 100 | 100 | 85 | 15.0% | 0.65 |
| smartrecruiters:sia | Sia | yes | OK | 100 | 100 | 74 | 26.0% | 0.64 |
| smartrecruiters:agileengine | AgileEngine | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:jadeglobal | Jade Global | yes | OK | 6 | 0 | 6 | 0.0% | 0.37 |
| smartrecruiters:version1 | Version 1 | yes | OK | 100 | 72 | 94 | 6.0% | 0.59 |
| smartrecruiters:informagroupplc | Informa Group Plc. | yes | OK | 100 | 89 | 80 | 20.0% | 0.39 |
| smartrecruiters:quantanite | Quantanite | yes | OK | 9 | 0 | 9 | 0.0% | 0.32 |
| smartrecruiters:blueoptima | BlueOptima | yes | OK | 11 | 6 | 11 | 0.0% | 0.32 |
| smartrecruiters:keywordsstudios | Keywords Studios | yes | OK | 35 | 5 | 31 | 11.4% | 0.39 |
| smartrecruiters:metromakro | METRO/MAKRO | yes | OK | 100 | 100 | 83 | 17.0% | 0.80 |
| smartrecruiters:nisum | Nisum | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:technogen | TechnoGen | yes | OK | 49 | 0 | 48 | 2.0% | 0.34 |
| smartrecruiters:vichara | Vichara Technologies | yes | OK | 9 | 3 | 9 | 0.0% | 0.42 |
| smartrecruiters:codeyoung | Codeyoung | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| smartrecruiters:capestart | CapeStart | yes | OK | 1 | 0 | 1 | 0.0% | 0.34 |
| smartrecruiters:genpactindia | Genpact India Pvt. Ltd. | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:servicetitan | ServiceTitan | yes | OK | 8 | 0 | 8 | 0.0% | 0.34 |
| smartrecruiters:rebelfoods | Rebel Foods | yes | OK | 1 | 0 | 1 | 0.0% | 0.37 |
| smartrecruiters:gepworldwide | GEP Worldwide | yes | OK | 1 | 0 | 1 | 0.0% | 0.37 |
| smartrecruiters:lingaro | Lingaro | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:pentair | Pentair | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:spottedzebra | Spotted Zebra | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:synechron | Synechron | yes | OK | 3 | 0 | 3 | 0.0% | 0.33 |
| breezy:iqvia | IQVIA | yes | OK | 7 | 0 | 6 | 14.3% | 0.38 |
| breezy:anovia | Anovia Inc. | yes | OK | 13 | 6 | 13 | 0.0% | 0.40 |
| breezy:foundationhealth | Foundation Health | yes | OK | 33 | 12 | 29 | 12.1% | 0.43 |
| remoteok | MULTIPLE | yes | OK | 99 | 24 | 99 | 0.0% | 1.33 |
| wwr | MULTIPLE | yes | FAIL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 4.75 |
| remotive | MULTIPLE | yes | OK | 20 | 13 | 20 | 0.0% | 0.26 |
| jobicy | MULTIPLE | yes | OK | 50 | 50 | 49 | 2.0% | 0.59 |
| himalayas | MULTIPLE | yes | OK | 200 | 200 | 196 | 2.0% | 2.51 |
| amazon | Amazon | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| jpmorgan | JPMorgan Chase | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| oracle | Oracle | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| accenture | Accenture | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| sap | SAP | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| optum | Optum | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
