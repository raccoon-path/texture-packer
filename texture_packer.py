import argparse
import json
import string
import time
from os import error
from pathlib import Path
from xmlrpc.client import Boolean
from PIL.Image import Image
from PIL import Image as Img
from PIL import ImageChops
import numpy as np
from argparse import ArgumentParser

description = '''\
This texture packer is tool for batch renaming and packing images to textures with custom channel layout.
|Implemented:
    Packing images to texture channels,
    Remapping texture suffixes.
    Pack only unexisting output textures (optional).
|Not implemented:
    Recursive directory scanning, glob syntax,
'''

parser  = ArgumentParser(epilog = description)
parser.add_argument("-c","--config", dest="config", default="config.txt", help="Path to config (relative cwd or absolute). Default 'config.txt' in cwd")
parser.add_argument("-s", "--src", dest="src_dir", default=None, help="Path to directory with source textures (relative cwd or absolute)")
parser.add_argument("-d","--dest", dest="dest_dir", default=None, help="Path to destination directory (relative cwd or absolute)")
parser.add_argument("-o","--output-format", dest= "output_format", default=None, help="Output format", choices=["png","jpg","bmp","tga","dds"])
parser.add_argument("--owerwrite", type=bool, dest="owerwrite", action=argparse.BooleanOptionalAction, help = "Owerwrite already existing packed output textures.")
#parser.add_argument("-l","-local-config", dest= "local_config", action="store_true", default="false", help="Use local config (defined in -c or --config) in source directory")
args = parser.parse_args()


class FileGroups:dict[str,dict[str,str]]
''' FileGroups STRUCTURE:
groups{
    group_name:{
        _src_suffix:path,
        _src_suffix2:path2
        ...
    },
    group_name_2:{
        ...
    },
    ...
}
'''
class PackChItem:
    
    suffix:str = ""
    ch:int = 0
    invert:bool = False

    def __init__(self, _suffix:str, ch:int=0, invert:bool = False) -> None:
        self.suffix = _suffix
        self.ch = ch
        self.invert = invert
        pass


class Config:
    ASSIGN_SIGN = ">"
    CHANNEL_SEPARATOR = ":"
    PIPELINE_SEPARATOR = "|"
    CHANNEL_INVERSION_SIGN = "*"
    SECTION_OPEN_SIGN = "["
    SECTION_CLOSE_SIGN = "]"
    COMMENT_SIGN = "#"
    NUM_TO_CH = {
        0:"r",
        1:"g",
        2:"b",
        3:"a"
    }

    CH_TO_NUM = {
        "r":0,
        "g":1,
        "b":2,
        "a":3,
        "*":4
    }
    
    #This is default values for my texture packing pipeline for Godot (albedo, orm, gl_normal, height), all this params may be overriden from config file defined in -c --config param
    src_dir = "" #may be overriden from -s --src param
    dest_dir = "dest" #may be overriden from -d --dest param
    lowercase_names = False
    output_format = "png" #may be overriden -o --output-format param
    owerwrite = True #ADDED, True to preserve old bahavior
    extensions=[".png",".jpg",".tga"]
    map_suffixes = {
        "_base_color":"_albedo",
        "_color":"_albedo",
        "_ambient_occlusion":"_ao",
        "_albedo":"",
        "_normal":"",
        "_ao":"",
        "_roughness":"",
        "_metallic":"",
        "_height":""
    }

    packer:dict[str:list[PackChItem]] = {
        "_albedo":[PackChItem("_albedo",0), PackChItem("_albedo",1),            PackChItem("_albedo",2)],
        "_orm":   [PackChItem("_ao"),       PackChItem("_roughness"),           PackChItem("_metallic")],
        "_normal":[PackChItem("_normal",0), PackChItem("_normal",1,invert=True),PackChItem("_normal",2)], 
    }

    

    def __init__(self) -> None:
        pass

    def _split_trim(self, line:str, separator:str)->list[str]:
        return [itm.strip() for itm in line.split(separator)]
    
    def _get_sections(self, lines:list[str])->dict[str:list[str]]:
        sections:dict[str:list[str]]={}
        current_section = None
        for line in lines:
            if line == "" or line.isspace(): continue #skip empty lines
            if line.startswith(self.COMMENT_SIGN): continue #skip comments
            
            if line.startswith(self.SECTION_OPEN_SIGN) and line.endswith(self.SECTION_CLOSE_SIGN): #section change
                current_section = line[1:-1]
                content = sections.get(current_section,None)
                if content == None:
                    sections[current_section] = []
                continue
                
            if current_section == None: continue #skip line without section or comment
            sections[current_section].append(line)
        return sections

    def _convert_auto(self, s:str)->any:
        #auto type conversion, terrible implementation, but works perfectly for me, suitable for small params count
        v = s.strip().lower()
        #int
        try:
            return int(v)
        except:
            pass
        #float
        try:
            return float(v)
        except:
            pass
        #bool
        if v == "true" or v == "false":
            return v == "true"
        
        #str if any other conversions didn`t work
        return s
       
    def _parse_pack_ch_items(self, s:str)->list[PackChItem]:
        items = []
        suff, data,*_= s.split(self.CHANNEL_SEPARATOR)
        for i in range(len(data)):
            ch = self.CH_TO_NUM[data[i]]
            if ch < 4:
                items.append(PackChItem(suff,ch))
            elif ch == 4:
                p = items.pop()
                p.invert = True
                items.append(p)
        return items
    
    def _parse_mapstr(self, mapstr:str):
        result = []
        for p in self._split_trim(mapstr,self.PIPELINE_SEPARATOR):
            result.extend(self._parse_pack_ch_items(p))
        return result

    def _packer_ch_to_text(self, item:PackChItem)->str:
        return item.suffix + ":" + self.NUM_TO_CH[item.ch] + ("*" if item.invert else "")

    def override_params(self, data:any):
        if not type(data) == dict:
            try:
                data = data.__dict__
            except:
                return
        for k, v in data.items():
            if v != None and hasattr(self,k):
                setattr(self,k,v)

    def load_from_file(self, file:str|Path):
        if file is not Path:
            file = Path(file)
        lines = []
        try:
            lines = [ln.strip() for ln in file.read_text().splitlines()]
        except error:
            print("[!] Config file: "+str(file)+" not loaded")
            return self
        sect = self._get_sections(lines)
        
        #attributes
        st = {itm[0]: self._convert_auto(itm[1]) for itm in    [self._split_trim(x,self.ASSIGN_SIGN) for x in sect["settings"]]}
        self.override_params(st)
        
        #filters/extensions
        flt = sect["filters"]
        self.extensions = [ex.strip() for ex in flt]
        
        #map_suffixes
        m_suf = sorted(sect["map suffixes"], key = lambda x: len(x), reverse = True) #sort long>short to avoid partially replaced suffixes
        ms = {sf[0]:("" if len(sf) < 2 else sf[1]) for sf in    [self._split_trim(x, self.ASSIGN_SIGN) for x in m_suf]}
        
        self.map_suffixes = ms
        #parse packer map
        p_lines = sect["pack"]
        p_map={}
        for ln in p_lines:
            map_suff,map_data,*_ = self._split_trim(ln, self.ASSIGN_SIGN)
            p_map[map_suff] = self._parse_mapstr(map_data)
        self.packer = p_map

        return self

    def save_to_file(self, path:str|Path):
        data:list[str] = []
        data.append("[settings]")
        data.append("lowercase_names > "+str(self.lowercase_names))
        data.append("scan_subdirectories > "+str(self.scan_subdirectories))
        data.append("save_format > "+str(self.save_format))
        data.append("src_dir > "+str(self.src_dir))
        data.append("dest_dir > "+str(self.dest_dir))
        data.append("owerwrite >"+ str(self.owerwrite))
        data.append("[filters]")
        for itm in self.extensions:
            data.append(itm)
        
        data.append("[map suffixes]")
        for k, v in self.map_suffixes.items():
            data.append(k+("" if (v.isspace() or v == "") else " > "+ v))
        
        data.append("[pack]")
        for k,v in self.packer.items():
            s = k+" > " + self._packer_ch_to_text(v[0])
            
            if len(v)>1:
                for i in range(1,len(v)):
                    s+=" | " + self._packer_ch_to_text(v[i])
            data.append(s)
        if path is not Path:
            path = Path(path)
        path.write_text("\n".join(data))
       

        

class TexturePacker:
    
    SUFFIX_PLACEHOLDER = "@S@"
    
    IMG_MODES_MAP = {
        1:"L",
        3:"RGB",
        4:"RGBA"
    }

    def __init__(self) -> None:
        pass
    
    def convert_mode_i_to_l(self, img:Image)->Image:
        array = np.uint8(np.array(img) / 256)
        return Img.fromarray(array)

    def load_image(self, path:str)->Image:
        try:
            return Img.open(path)
        except error:
            print("[!] Image <"+str(path)+"> not loaded.")
            return None

    def get_file_suffix_index(self, name:str, suffixes:list[str] )-> tuple[str, int]:
        name = name.lower()
        for s in suffixes:
            index = name.rfind(s)
            if index > -1:
                return s, index
        return None, -1

    def get_mapped_suffix(self,suffix:str, suffix_map:dict[str,str])->str:
        mapped = suffix_map.get(suffix,"")
        return suffix if mapped == "" else mapped

    def get_groups(self, paths:list[Path], relative_to:Path, suffixes_map:dict[str,str])->dict[str,dict[str:Path]]:
        '''
        output:
        {
            group_name_1:{
                _suffix1:path1,
                ...,
                _suffix_n:path_n,
            },

            ...,

            group_name_n{
                ...
            }
        }
        '''
        suffixes = suffixes_map.keys()
        groups = {}
        for pth in paths:
            sf, sf_index = self.get_file_suffix_index(pth.stem,suffixes)

            if sf == None:
                print("[-] Skip: "+str(pth)+" (has no valid suffix, described in [map suffixes] section of config)")
                continue
            
            grp_name = str(pth.relative_to(relative_to)).rsplit(".",1)[0]
            grp_name = grp_name[ :sf_index] + self.SUFFIX_PLACEHOLDER + grp_name[sf_index + len(sf): ]
            
            itms = groups.get(grp_name, None)
            if itms == None:
                itms = {}
                groups[grp_name] = itms
            itms[self.get_mapped_suffix(sf,suffixes_map)] = pth
        return groups

    def get_filtered_packer_config(self, group_name:str, target_dir:Path)->dict[str, list[PackChItem]]:
        pk_conf = {}
        for pk_suffix in config.packer:
            excl_path = target_dir.joinpath(group_name + pk_suffix+"."+config.output_format)
            if not excl_path.exists():
                pk_conf[pk_suffix]=config.packer[pk_suffix]
            else:
                print("[-] Skip: " + str(excl_path) + " (file exists)")
        return pk_conf

    def load_texture_bands(self, group_items:dict[str,Path], config:dict[list[PackChItem]])->dict[str,list[Image]]:
        loaded:dict[str,list[Image]] = {}
        for pack_grp in config:
            pack_ch_itms = config[pack_grp]
            for pack_ch_itm in pack_ch_itms:
                band_path = group_items.get(pack_ch_itm.suffix, None)
                if band_path!=None and band_path.exists() and (loaded.get(pack_ch_itm.suffix, None) == None):
                    img = self.load_image(band_path)
                    if img != None:
                        loaded[pack_ch_itm.suffix] = img.split()
                    else:
                        loaded[pack_ch_itm.suffix] = None
        return loaded
    
    def pack_texture(self,band_lookup:dict[str,list[Image]], pack_items:list[PackChItem])->Image:
        if len(band_lookup) < 1: return None
        
        black_ch = Img.new("L",list(band_lookup.values())[0][0].size,0) # value[0].band[0] - determine size by first band img and create black ch 
        ch_bands:list[Image] = []
        
        for item in pack_items:
            g_tex = band_lookup.get(item.suffix, [])
            if item.ch<len(g_tex):
                bnd = g_tex[item.ch]
                if bnd.mode == "I":
                    #print("Item: "+item.suffix+" has I (32 bits per channel) format, convert to L(8 bits per channel).")
                    bnd = self.convert_mode_i_to_l(bnd)

                bnd = bnd if not item.invert else ImageChops.invert(bnd)
                ch_bands.append(bnd)
            else:
                ch_bands.append(black_ch)
            #print("-->: ["+item.suffix+"] contains "+str(len(g_tex))+" channels")
        
        if len(ch_bands)==2: #two channels unavailable, remove last one
            ch_bands.pop()
        img =  Img.merge(self.IMG_MODES_MAP[len(ch_bands)],ch_bands)
        return img

    def pack_material_stems(self, group_items:dict[str,Path], config:dict[str:list[PackChItem]]):
        packed_stems:dict[str,Image] = {}
        bands = self.load_texture_bands(group_items,config)
        for itm_name in config:
            #print("- texture: "+itm_name)
            tex = self.pack_texture(bands,config[itm_name])
            packed_stems[itm_name] = tex
        return packed_stems

    def pack_textures(self, config:Config):
        
        src_dir = Path(config.src_dir).resolve()
        target_dir = Path(config.dest_dir).resolve()
        dest_is_src = src_dir == target_dir
        
        if not src_dir.exists():
            print("[!] Src directory <"+str(src_dir)+"> does not exists")
            exit(1)
        
        src_files = [fl for fl in src_dir.iterdir() if fl.suffix.lower() in config.extensions]

        groups = self.get_groups(src_files, src_dir, config.map_suffixes)
        
        for grp_name in groups:
            # Filter pack items is (owerwrite==True)
            pk_conf = config.packer if config.owerwrite else self.get_filtered_packer_config(grp_name, target_dir)
            tex_lookup = self.pack_material_stems(groups[grp_name], pk_conf)
            
            t_dir = target_dir.joinpath(grp_name).parent
            if not t_dir.exists():
                print("[!] Directory <"+str(t_dir)+"> does not exists, create it..")
                t_dir.mkdir(parents=True)
            
            #save packed textures
            for tex_suffix in tex_lookup:
                save_path = target_dir.joinpath(grp_name.replace(self.SUFFIX_PLACEHOLDER, tex_suffix)+"."+config.output_format).resolve()
                #prevent silent overwrite sources
                if dest_is_src and save_path.exists():
                    print("[?] OVERWRITE SOURCE FILE: <"+str(save_path)+"> ?")
                    print(" -> [Y] [ENTER] to overwrite") 
                    answ = input()   
                    if answ.lower() !="y":
                        print("[!] Cancel")
                        continue
                #lowercase names on save
                if config.lowercase_names:
                    save_path = str(save_path).lower()
                if tex_lookup[tex_suffix] != None: #if output texture suffix described in config.packer but no source texture channels exists, <None> goes here, nasty bug fixed!
                    tex_lookup[tex_suffix].save(save_path,config.output_format) # finally, save the file
                    print("[+] Save: "+str(save_path))

if __name__ == "__main__":

   
    tmr = time.perf_counter()
    config = Config().load_from_file(args.config)
    
    #override config params from commandline
    config.override_params(args.__dict__)
    
    if config.owerwrite:
        print("[!] OWERWRITE mode: all output textures will be owerwritten.")
    else:
        print("[!] NO-OWERWRITE mode: existing files will not owerwritten")

    packer = TexturePacker()
    packer.pack_textures(config)


    tmr = round((time.perf_counter()-tmr))
    print("Texture packing complete. Elapsed time: "+str(tmr) + " s")

