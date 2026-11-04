"""Page/region provenance carried in existing JSONB chunk locators; no migration."""
from dataclasses import dataclass,field


@dataclass
class PageContent:
    text:str
    source_locator:dict
    page_number:int|None=None
    tables:list=field(default_factory=list)
    visual_regions:list=field(default_factory=list)
    extraction_method:str='native'
    warnings:list=field(default_factory=list)

    def unit(self):
        locator={**self.source_locator,'extraction_method':self.extraction_method,
                 'coverage':'complete','warnings':self.warnings}
        if self.page_number is not None:
            locator['page']=self.page_number
        if self.visual_regions:
            locator.update(visual=True,visual_regions=self.visual_regions)
        return self.text,locator
