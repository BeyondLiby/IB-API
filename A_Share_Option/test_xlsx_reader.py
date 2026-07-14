from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from A_Share_Option.xlsx_reader import read_option_chain


def write_minimal_workbook(path: Path) -> None:
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>代码</t></is></c>
      <c r="B1" t="inlineStr"><is><t>最新价</t></is></c>
      <c r="C1" t="inlineStr"><is><t>Mid</t></is></c>
      <c r="D1" t="inlineStr"><is><t>Bid</t></is></c>
      <c r="E1" t="inlineStr"><is><t>Ask</t></is></c>
      <c r="F1" t="inlineStr"><is><t>涨跌</t></is></c>
      <c r="G1" t="inlineStr"><is><t>涨跌幅</t></is></c>
      <c r="H1" t="inlineStr"><is><t>成交量</t></is></c>
      <c r="I1" t="inlineStr"><is><t>持仓量</t></is></c>
      <c r="J1" t="inlineStr"><is><t>IV</t></is></c>
      <c r="K1" t="inlineStr"><is><t>MIV</t></is></c>
      <c r="L1" t="inlineStr"><is><t>Delta</t></is></c>
      <c r="M1" t="inlineStr"><is><t>Gamma</t></is></c>
      <c r="N1" t="inlineStr"><is><t>Vega</t></is></c>
      <c r="O1" t="inlineStr"><is><t>Theta</t></is></c>
      <c r="P1" t="inlineStr"><is><t>到期日</t></is></c>
      <c r="Q1" t="inlineStr"><is><t>方向</t></is></c>
      <c r="R1" t="inlineStr"><is><t>保证金</t></is></c>
      <c r="S1" t="inlineStr"><is><t>现券价格</t></is></c>
      <c r="T1" t="inlineStr"><is><t>行权价</t></is></c>
    </row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>90000001.SZ</t></is></c>
      <c r="B2"><v>0.12</v></c>
      <c r="C2"><v>0.13</v></c>
      <c r="D2"><v>0.12</v></c>
      <c r="E2"><v>0.14</v></c>
      <c r="F2"><v>0.01</v></c>
      <c r="G2"><v>8.33</v></c>
      <c r="H2"><v>100</v></c>
      <c r="I2"><v>200</v></c>
      <c r="J2"><v>0.25</v></c>
      <c r="K2"><v>0.26</v></c>
      <c r="L2"><v>0.52</v></c>
      <c r="M2"><v>0.02</v></c>
      <c r="N2"><v>0.01</v></c>
      <c r="O2"><v>-0.01</v></c>
      <c r="P2"><v>10</v></c>
      <c r="Q2" t="inlineStr"><is><t>认购</t></is></c>
      <c r="R2"><v>3000</v></c>
      <c r="S2"><v>4.04</v></c>
      <c r="T2"><v>4.1</v></c>
    </row>
  </sheetData>
</worksheet>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        archive.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="创业板" sheetId="1" r:id="rId1"/></sheets>
</workbook>""")
        archive.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""")
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class XlsxReaderTest(unittest.TestCase):
    def test_reads_option_chain_and_optional_strike(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chain.xlsx"
            write_minimal_workbook(path)
            payload = read_option_chain(path, "创业板")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["rowCount"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["code"], "90000001.SZ")
        self.assertEqual(row["product"], "创业")
        self.assertEqual(row["optionType"], "认购")
        self.assertEqual(row["strike"], 4.1)
        self.assertEqual(row["dte"], 10)
        self.assertEqual(row["mark"], 0.13)


if __name__ == "__main__":
    unittest.main()

