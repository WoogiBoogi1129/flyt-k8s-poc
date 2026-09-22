from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime/shm/control'))
from domain_hook import pci_location

class PCITests(unittest.TestCase):
    def domain(self,slots):
        root=ET.fromstring('<domain><devices><controller type="pci" index="0" model="pcie-root"/></devices></domain>')
        for slot in slots:
            dev=ET.SubElement(root.find('devices'),'controller')
            ET.SubElement(dev,'address',{'type':'pci','bus':'0x00','slot':hex(slot)})
        return root
    def test_avoids_vga_and_all_declared_root_devices(self):
        self.assertEqual(pci_location(self.domain([1,2,3,30])),('pcie.0',29))
    def test_full_bus_is_rejected(self):
        with self.assertRaises(ValueError):pci_location(self.domain(range(1,31)))
    def test_child_bus_slot_does_not_conflict(self):
        root=self.domain([30]);root.find('.//address').set('bus','0x01')
        self.assertEqual(pci_location(root),('pcie.0',30))
    def test_unsupported_root_is_rejected(self):
        root=self.domain([]);root.find('.//controller').set('model','pcie-expander-bus')
        with self.assertRaises(ValueError):pci_location(root)
    def test_kubevirt_pre_libvirt_implicit_root(self):
        root=ET.fromstring('<domain><os><type machine="pc-q35-rhel9.8.0">hvm</type></os><devices/></domain>')
        self.assertEqual(pci_location(root),('pcie.0',30))
    def test_unknown_implicit_root_rejected(self):
        root=ET.fromstring('<domain><os><type machine="virt"/></os><devices/></domain>')
        with self.assertRaises(ValueError):pci_location(root)

if __name__=='__main__':unittest.main()
